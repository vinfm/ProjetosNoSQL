#!/usr/bin/env python3
"""Validador das respostas das queries TPC-H no MongoDB contra referências MySQL."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
import datetime
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Literal

try:
    from bson.decimal128 import Decimal128
    from bson.int64 import Int64
    from bson.objectid import ObjectId
except ImportError:  # pragma: no cover - fallback when pymongo/bson is absent
    Decimal128 = None  # type: ignore[assignment]
    Int64 = None  # type: ignore[assignment]
    ObjectId = None  # type: ignore[assignment]


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_MONGO_DIR = BASE_DIR / "queries_tpch_mongodb"
DEFAULT_MYSQL_DIR = BASE_DIR / "queries_tpch_mysql"
DEFAULT_REF_DIR = BASE_DIR

DEFAULT_MONGO_URI = os.environ.get(
    "MONGO_URI",
    "mongodb://localhost:27021,localhost:27022,localhost:27023/?replicaSet=rs0",
)
DEFAULT_MONGO_DB = os.environ.get("MONGO_DB", "tpch")
DEFAULT_MYSQL_HOST = os.environ.get("MYSQL_HOST", "localhost")
DEFAULT_MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
DEFAULT_MYSQL_USER = os.environ.get("MYSQL_USER", "root")
DEFAULT_MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "1234")
DEFAULT_MYSQL_DB = os.environ.get("MYSQL_DB", "tpch")

BLOCK_SEPARATOR_RE = re.compile(r"^\s*//\s*={3,}\s*$", re.MULTILINE)
AGGREGATE_COMMENT_RE = re.compile(r"db\.([A-Za-z_][A-Za-z0-9_]*)\.aggregate")
NUMBER_RE = re.compile(r"-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
IDENT_START_RE = re.compile(r"[$A-Za-z_]")
IDENT_CONT_RE = re.compile(r"[$A-Za-z0-9_]")
DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
INT_STR_RE = re.compile(r"^[+-]?\d+$")

FieldKind = Literal["str", "int", "float", "avg", "date"]


class MongoParseError(ValueError):
    """Erro de sintaxe ao interpretar arquivo .mongodb."""


@dataclass(frozen=True)
class FieldSpec:
    canonical: str
    ref_name: str
    aliases: tuple[str, ...]
    kind: FieldKind


@dataclass(frozen=True)
class QuerySchema:
    query_id: int
    name: str
    key_fields: tuple[str, ...]
    fields: tuple[FieldSpec, ...]

    def alias_map(self) -> dict[str, FieldSpec]:
        mapping: dict[str, FieldSpec] = {}
        for f in self.fields:
            for alias in f.aliases:
                mapping[alias.lower()] = f
            mapping[f.canonical.lower()] = f
            mapping[f.ref_name.lower()] = f
        return mapping

    def field_by_canonical(self, canonical: str) -> FieldSpec:
        for f in self.fields:
            if f.canonical == canonical:
                return f
        raise KeyError(f"Campo desconhecido: {canonical}")


QUERY_SCHEMAS: dict[int, QuerySchema] = {
    1: QuerySchema(
        query_id=1,
        name="Pricing Summary Report (Q1)",
        key_fields=("l_returnflag", "l_linestatus"),
        fields=(
            FieldSpec("l_returnflag", "returnflag", ("l_returnflag", "returnflag"), "str"),
            FieldSpec("l_linestatus", "linestatus", ("l_linestatus", "linestatus"), "str"),
            FieldSpec("sum_qty", "sum_qty", ("sum_qty",), "float"),
            FieldSpec("sum_base_price", "sum_base_price", ("sum_base_price",), "float"),
            FieldSpec("sum_disc_price", "sum_disc_price", ("sum_disc_price",), "float"),
            FieldSpec("sum_charge", "sum_charge", ("sum_charge",), "float"),
            FieldSpec("avg_qty", "avg_qty", ("avg_qty",), "avg"),
            FieldSpec("avg_price", "avg_price", ("avg_price",), "avg"),
            FieldSpec("avg_disc", "avg_disc", ("avg_disc",), "avg"),
            FieldSpec("count_order", "count_order", ("count_order",), "int"),
        ),
    ),
    2: QuerySchema(
        query_id=2,
        name="Minimum Cost Supplier (Q2)",
        key_fields=("p_partkey", "s_name"),
        fields=(
            FieldSpec("s_acctbal", "acctbal", ("s_acctbal", "acctbal"), "float"),
            FieldSpec("s_name", "supplier_name", ("s_name", "supplier_name"), "str"),
            FieldSpec("n_name", "nation_name", ("n_name", "nation_name"), "str"),
            FieldSpec("p_partkey", "partkey", ("p_partkey", "partkey"), "int"),
            FieldSpec("p_mfgr", "mfgr", ("p_mfgr", "mfgr"), "str"),
            FieldSpec("s_address", "address", ("s_address", "address"), "str"),
            FieldSpec("s_phone", "phone", ("s_phone", "phone"), "str"),
            FieldSpec("s_comment", "supplier_comment", ("s_comment", "supplier_comment"), "str"),
        ),
    ),
    3: QuerySchema(
        query_id=3,
        name="Shipping Priority (Q3)",
        key_fields=("l_orderkey",),
        fields=(
            FieldSpec("l_orderkey", "orderkey", ("l_orderkey", "orderkey"), "int"),
            FieldSpec("revenue", "revenue", ("revenue",), "float"),
            FieldSpec("o_orderdate", "orderdate", ("o_orderdate", "orderdate"), "date"),
            FieldSpec("o_shippriority", "shippriority", ("o_shippriority", "shippriority"), "int"),
        ),
    ),
}


@dataclass(frozen=True)
class Token:
    kind: str
    value: Any
    line: int
    col: int


@dataclass
class MongoQuerySpec:
    query_id: int
    source_file: Path
    block_index: int
    collection: str
    pipeline: list[dict[str, Any]]
    options: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _parse_iso_datetime(text: str) -> datetime.datetime:
    s = text.strip()
    if DATE_ONLY_RE.match(s):
        d = datetime.date.fromisoformat(s)
        return datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc)

    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"

    # Python 3.10 fromisoformat aceita 3 ou 6 dígitos fracionários.
    frac_match = re.match(r"^(.*\.\d+)([+-]\d{2}:\d{2})?$", s)
    if frac_match:
        prefix, tz_part = frac_match.group(1), frac_match.group(2) or ""
        head, frac = prefix.rsplit(".", 1)
        if len(frac) not in (3, 6):
            frac = (frac + "000000")[:6]
        s = f"{head}.{frac}{tz_part}"

    dt = datetime.datetime.fromisoformat(s)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def _tokenize_mongo_block(text: str) -> tuple[list[Token], list[str]]:
    tokens: list[Token] = []
    comments: list[str] = []
    n = len(text)
    i = 0
    line = 1
    col = 1

    def advance(chars: int = 1) -> None:
        nonlocal i, line, col
        for _ in range(chars):
            if text[i] == "\n":
                line += 1
                col = 1
            else:
                col += 1
            i += 1

    while i < n:
        ch = text[i]

        if ch.isspace():
            advance()
            continue

        # Comentário de linha //
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            start = i + 2
            advance(2)
            while i < n and text[i] != "\n":
                advance()
            comments.append(text[start:i])
            continue

        # Comentário de bloco /* ... */
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            start_line, start_col = line, col
            start = i + 2
            advance(2)
            closed = False
            while i + 1 < n:
                if text[i] == "*" and text[i + 1] == "/":
                    comments.append(text[start:i])
                    advance(2)
                    closed = True
                    break
                advance()
            if not closed:
                raise MongoParseError(
                    f"Comentário de bloco não fechado (linha {start_line}, coluna {start_col})."
                )
            continue

        # Strings com aspas duplas ou simples
        if ch in ('"', "'"):
            quote = ch
            start_line, start_col = line, col
            advance()
            chars: list[str] = []
            escape_map = {
                '"': '"',
                "'": "'",
                "\\": "\\",
                "/": "/",
                "b": "\b",
                "f": "\f",
                "n": "\n",
                "r": "\r",
                "t": "\t",
            }
            closed = False
            while i < n:
                c = text[i]
                if c == quote:
                    advance()
                    closed = True
                    break
                if c == "\\":
                    advance()
                    if i >= n:
                        raise MongoParseError(
                            f"Escape incompleto em string (linha {start_line}, coluna {start_col})."
                        )
                    esc = text[i]
                    if esc in escape_map:
                        chars.append(escape_map[esc])
                        advance()
                    elif esc == "u":
                        if i + 4 >= n:
                            raise MongoParseError(
                                f"Escape unicode inválido (linha {line}, coluna {col})."
                            )
                        hex_digits = text[i + 1 : i + 5]
                        try:
                            chars.append(chr(int(hex_digits, 16)))
                        except ValueError as exc:
                            raise MongoParseError(
                                f"Escape unicode inválido '\\u{hex_digits}' (linha {line}, coluna {col})."
                            ) from exc
                        advance(5)
                    else:
                        chars.append(esc)
                        advance()
                else:
                    chars.append(c)
                    advance()
            if not closed:
                raise MongoParseError(
                    f"String não fechada (linha {start_line}, coluna {start_col})."
                )
            tokens.append(Token("STRING", "".join(chars), start_line, start_col))
            continue

        # Pontuação
        if ch in "[]{}:,()":
            tokens.append(Token(ch, ch, line, col))
            advance()
            continue

        # Números (incluindo negativos e decimais)
        if ch.isdigit() or (
            ch == "-" and i + 1 < n and (text[i + 1].isdigit() or text[i + 1] == ".")
        ):
            num_match = NUMBER_RE.match(text, i)
            if num_match:
                raw = num_match.group(0)
                start_line, start_col = line, col
                advance(len(raw))
                val: int | float
                if "." in raw or "e" in raw or "E" in raw:
                    val = float(raw)
                else:
                    val = int(raw)
                tokens.append(Token("NUMBER", val, start_line, start_col))
                continue

        # Identificadores
        if IDENT_START_RE.match(ch):
            start_line, start_col = line, col
            start = i
            advance()
            while i < n and IDENT_CONT_RE.match(text[i]):
                advance()
            tokens.append(Token("IDENT", text[start:i], start_line, start_col))
            continue

        raise MongoParseError(
            f"Caractere inesperado {ch!r} na linha {line}, coluna {col}."
        )

    tokens.append(Token("EOF", None, line, col))
    return tokens, comments


class _MongoBlockParser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    @property
    def current(self) -> Token:
        return self.tokens[self.pos]

    def peek(self, offset: int = 1) -> Token:
        idx = self.pos + offset
        if idx < len(self.tokens):
            return self.tokens[idx]
        return self.tokens[-1]

    def expect(self, kind: str) -> Token:
        tok = self.current
        if tok.kind != kind:
            raise MongoParseError(
                f"Esperado {kind!r}, encontrado {tok.kind!r} ({tok.value!r}) "
                f"na linha {tok.line}, coluna {tok.col}."
            )
        self.pos += 1
        return tok

    def parse_value(self) -> Any:
        tok = self.current
        if tok.kind == "[":
            return self.parse_array()
        if tok.kind == "{":
            return self.parse_object()
        if tok.kind in ("STRING", "NUMBER"):
            self.pos += 1
            return tok.value
        if tok.kind == "IDENT":
            return self._parse_ident_value()
        raise MongoParseError(
            f"Valor inesperado {tok.kind!r} ({tok.value!r}) na linha {tok.line}, coluna {tok.col}."
        )

    def _parse_single_arg_call(self, fn_name: str, allow_empty: bool = False) -> Any:
        self.expect("(")
        if allow_empty and self.current.kind == ")":
            self.pos += 1
            return None
        arg = self.parse_value()
        if self.current.kind == ",":
            raise MongoParseError(
                f"{fn_name} aceita apenas 1 argumento (linha {self.current.line}, coluna {self.current.col})."
            )
        self.expect(")")
        return arg

    def _parse_ident_value(self) -> Any:
        tok = self.expect("IDENT")
        name = tok.value

        if name == "true":
            return True
        if name == "false":
            return False
        if name == "null":
            return None

        if name == "ISODate":
            arg = self._parse_single_arg_call("ISODate", allow_empty=True)
            if arg is None:
                return datetime.datetime.now(datetime.timezone.utc)
            if not isinstance(arg, str):
                raise MongoParseError(
                    f"ISODate espera string ISO (linha {tok.line}, coluna {tok.col})."
                )
            try:
                return _parse_iso_datetime(arg)
            except ValueError as exc:
                raise MongoParseError(
                    f"ISODate inválido {arg!r} (linha {tok.line}, coluna {tok.col}): {exc}"
                ) from exc

        if name == "new":
            ctor = self.expect("IDENT")
            if ctor.value != "Date":
                raise MongoParseError(
                    f"Construtor 'new {ctor.value}' não suportado (linha {ctor.line}, coluna {ctor.col})."
                )
            return self._parse_date_ctor(ctor)

        if name == "Date" and self.current.kind == "(":
            return self._parse_date_ctor(tok)

        if name == "NumberInt":
            arg = self._parse_single_arg_call("NumberInt")
            try:
                return int(arg)
            except (TypeError, ValueError) as exc:
                raise MongoParseError(
                    f"NumberInt inválido {arg!r} (linha {tok.line}, coluna {tok.col})."
                ) from exc

        if name == "NumberLong":
            arg = self._parse_single_arg_call("NumberLong")
            try:
                val = int(arg)
            except (TypeError, ValueError) as exc:
                raise MongoParseError(
                    f"NumberLong inválido {arg!r} (linha {tok.line}, coluna {tok.col})."
                ) from exc
            return Int64(val) if Int64 is not None else val

        if name == "NumberDecimal":
            arg = self._parse_single_arg_call("NumberDecimal")
            if not isinstance(arg, (str, int, float)) or isinstance(arg, bool):
                raise MongoParseError(
                    f"NumberDecimal inválido {arg!r} (linha {tok.line}, coluna {tok.col})."
                )
            text_val = str(arg)
            return Decimal128(text_val) if Decimal128 is not None else Decimal(text_val)

        raise MongoParseError(
            f"Identificador não suportado {name!r} na linha {tok.line}, coluna {tok.col}."
        )

    def _parse_date_ctor(self, tok: Token) -> datetime.datetime:
        arg = self._parse_single_arg_call("Date", allow_empty=True)
        if arg is None:
            return datetime.datetime.now(datetime.timezone.utc)
        if isinstance(arg, str):
            try:
                return _parse_iso_datetime(arg)
            except ValueError as exc:
                raise MongoParseError(
                    f"Date inválido {arg!r} (linha {tok.line}, coluna {tok.col}): {exc}"
                ) from exc
        if isinstance(arg, (int, float)) and not isinstance(arg, bool):
            return datetime.datetime.fromtimestamp(arg / 1000.0, tz=datetime.timezone.utc)
        raise MongoParseError(
            f"Date espera string ISO ou timestamp em ms (linha {tok.line}, coluna {tok.col})."
        )

    def parse_array(self) -> list[Any]:
        self.expect("[")
        items: list[Any] = []
        while self.current.kind != "]":
            items.append(self.parse_value())
            if self.current.kind == ",":
                self.pos += 1
                if self.current.kind == "]":
                    break
            elif self.current.kind != "]":
                tok = self.current
                raise MongoParseError(
                    f"Esperado ',' ou ']', encontrado {tok.kind!r} na linha {tok.line}, coluna {tok.col}."
                )
        self.expect("]")
        return items

    def _parse_key(self) -> str:
        tok = self.current
        if tok.kind in ("STRING", "IDENT"):
            self.pos += 1
            return str(tok.value)
        raise MongoParseError(
            f"Chave de objeto esperada, encontrado {tok.kind!r} ({tok.value!r}) "
            f"na linha {tok.line}, coluna {tok.col}."
        )

    def parse_object(self) -> dict[str, Any]:
        self.expect("{")
        obj: dict[str, Any] = {}
        while self.current.kind != "}":
            key = self._parse_key()
            self.expect(":")
            val = self.parse_value()
            obj[key] = val
            if self.current.kind == ",":
                self.pos += 1
                if self.current.kind == "}":
                    break
            elif self.current.kind != "}":
                tok = self.current
                raise MongoParseError(
                    f"Esperado ',' ou '}}', encontrado {tok.kind!r} na linha {tok.line}, coluna {tok.col}."
                )
        self.expect("}")
        return obj

    def parse_bare_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {}
        while self.current.kind != "EOF":
            key = self._parse_key()
            self.expect(":")
            val = self.parse_value()
            options[key] = val
            if self.current.kind == ",":
                self.pos += 1
                if self.current.kind == "EOF":
                    break
            elif self.current.kind != "EOF":
                tok = self.current
                raise MongoParseError(
                    f"Esperado ',' ou fim do bloco nas opções, encontrado {tok.kind!r} "
                    f"na linha {tok.line}, coluna {tok.col}."
                )
        return options


def parse_mongodb_file(path: Path, query_id: int | None = None) -> list[MongoQuerySpec]:
    """Lê e interpreta um arquivo .mongodb anotado com múltiplos pipelines."""
    content = path.read_text(encoding="utf-8")
    if query_id is None:
        try:
            query_id = int(path.stem)
        except ValueError as exc:
            raise MongoParseError(
                f"Nome de arquivo .mongodb deve ser numérico (ex.: 1.mongodb): {path}"
            ) from exc

    raw_blocks = BLOCK_SEPARATOR_RE.split(content)
    specs: list[MongoQuerySpec] = []

    for block_idx, block_text in enumerate(raw_blocks, start=1):
        tokens, comments = _tokenize_mongo_block(block_text)
        if tokens[0].kind == "EOF":
            continue

        comment_text = "\n".join(comments)
        raw_colls = AGGREGATE_COMMENT_RE.findall(comment_text)
        collections = list(dict.fromkeys(raw_colls))
        if not collections:
            raise MongoParseError(
                f"{path.name} (bloco {block_idx}): nenhuma anotação 'db.<colecao>.aggregate' encontrada."
            )

        parser = _MongoBlockParser(tokens)
        if parser.current.kind != "[":
            tok = parser.current
            raise MongoParseError(
                f"{path.name} (bloco {block_idx}): esperado array de pipeline '[...]', "
                f"encontrado {tok.kind!r} na linha {tok.line}, coluna {tok.col}."
            )
        pipeline = parser.parse_array()

        options: dict[str, Any] = {}
        warnings: list[str] = []
        if parser.current.kind == ",":
            parser.expect(",")
            if parser.current.kind == "EOF":
                pass
            elif parser.current.kind == "{":
                options = parser.parse_object()
            elif parser.current.kind in ("IDENT", "STRING"):
                warnings.append(
                    f"{path.name} (bloco {block_idx}): opções após o pipeline estão sem chaves "
                    f"'{{ ... }}'; interpretadas automaticamente."
                )
                options = parser.parse_bare_options()
            else:
                tok = parser.current
                raise MongoParseError(
                    f"{path.name} (bloco {block_idx}): formato inválido para opções após vírgula "
                    f"na linha {tok.line}, coluna {tok.col}."
                )

        if parser.current.kind != "EOF":
            tok = parser.current
            raise MongoParseError(
                f"{path.name} (bloco {block_idx}): conteúdo extra inesperado {tok.kind!r} "
                f"({tok.value!r}) na linha {tok.line}, coluna {tok.col}."
            )

        for coll in collections:
            specs.append(
                MongoQuerySpec(
                    query_id=query_id,
                    source_file=path,
                    block_index=block_idx,
                    collection=coll,
                    pipeline=pipeline,
                    options=dict(options),
                    warnings=list(warnings),
                )
            )

    return specs


def discover_mongo_queries(mongo_dir: Path = DEFAULT_MONGO_DIR) -> list[MongoQuerySpec]:
    """Descobre e interpreta todos os arquivos <id>.mongodb ordenados numericamente."""
    if not mongo_dir.is_dir():
        raise FileNotFoundError(f"Diretório não encontrado: {mongo_dir}")

    files: list[tuple[int, Path]] = []
    for p in mongo_dir.glob("*.mongodb"):
        if p.stem.isdigit():
            files.append((int(p.stem), p))
    files.sort(key=lambda item: item[0])

    all_specs: list[MongoQuerySpec] = []
    for qid, path in files:
        all_specs.extend(parse_mongodb_file(path, query_id=qid))
    return all_specs


def unwrap_ejson(val: Any) -> Any:
    """Converte tipos BSON/PyMongo e Extended JSON para tipos Python padrão."""
    if Decimal128 is not None and isinstance(val, Decimal128):
        return val.to_decimal()
    if Int64 is not None and isinstance(val, Int64):
        return int(val)
    if ObjectId is not None and isinstance(val, ObjectId):
        return str(val)

    if isinstance(val, dict) and len(val) == 1:
        key, inner = next(iter(val.items()))
        if key == "$date":
            if isinstance(inner, dict) and "$numberLong" in inner and len(inner) == 1:
                ms = int(inner["$numberLong"])
                return datetime.datetime.fromtimestamp(ms / 1000.0, tz=datetime.timezone.utc)
            if isinstance(inner, (int, float)) and not isinstance(inner, bool):
                return datetime.datetime.fromtimestamp(inner / 1000.0, tz=datetime.timezone.utc)
            if isinstance(inner, str):
                return _parse_iso_datetime(inner)
        elif key == "$numberInt":
            return int(inner)
        elif key == "$numberLong":
            return int(inner)
        elif key == "$numberDouble":
            return float(inner)
        elif key == "$numberDecimal":
            return Decimal(str(inner))

    return val


def _to_exact_int(val: Any) -> tuple[int | None, str | None]:
    v = unwrap_ejson(val)
    if isinstance(v, bool):
        return None, f"esperado int, recebido bool ({v!r})"
    if isinstance(v, int):
        return v, None
    if isinstance(v, Decimal):
        if not v.is_finite():
            return None, f"Decimal não finito ({v!r})"
        if v == v.to_integral_value():
            return int(v), None
        return None, f"esperado inteiro exato, recebido Decimal fracionário ({v!r})"
    if isinstance(v, str):
        s = v.strip()
        if INT_STR_RE.match(s):
            return int(s), None
        return None, f"string não representa inteiro válido ({v!r})"
    return None, f"esperado int, recebido {type(v).__name__} ({v!r})"


def _to_decimal(val: Any) -> tuple[Decimal | None, str | None]:
    v = unwrap_ejson(val)
    if isinstance(v, bool):
        return None, f"esperado número, recebido bool ({v!r})"
    if isinstance(v, Decimal):
        if not v.is_finite():
            return None, f"Decimal não finito ({v!r})"
        return v, None
    if isinstance(v, int):
        return Decimal(v), None
    if isinstance(v, float):
        if not math.isfinite(v):
            return None, f"float não finito ({v!r})"
        return Decimal(str(v)), None
    if isinstance(v, str):
        s = v.strip()
        try:
            d = Decimal(s)
        except InvalidOperation:
            return None, f"string numérica inválida ({v!r})"
        if not d.is_finite():
            return None, f"número não finito ({v!r})"
        return d, None
    return None, f"esperado número, recebido {type(v).__name__} ({v!r})"


def _to_date_str(val: Any) -> tuple[str | None, str | None]:
    v = unwrap_ejson(val)
    if isinstance(v, datetime.datetime):
        dt = v.astimezone(datetime.timezone.utc) if v.tzinfo is not None else v
        if (dt.hour, dt.minute, dt.second, dt.microsecond) != (0, 0, 0, 0):
            return None, f"datetime com horário diferente de 00:00:00 ({v.isoformat()})"
        return dt.date().isoformat(), None
    if isinstance(v, datetime.date):
        return v.isoformat(), None
    if isinstance(v, str):
        s = v.strip()
        if DATE_ONLY_RE.match(s):
            try:
                return datetime.date.fromisoformat(s).isoformat(), None
            except ValueError as exc:
                return None, f"data inválida ({v!r}): {exc}"
        try:
            dt = _parse_iso_datetime(s)
        except ValueError as exc:
            return None, f"data/datetime ISO inválido ({v!r}): {exc}"
        if (dt.hour, dt.minute, dt.second, dt.microsecond) != (0, 0, 0, 0):
            return None, f"datetime com horário diferente de 00:00:00 ({v!r})"
        return dt.date().isoformat(), None
    return None, f"esperado data, recebido {type(v).__name__} ({v!r})"


def normalize_row(
    schema: QuerySchema, row: dict[str, Any]
) -> tuple[dict[str, Any], list[str], list[str], list[str]]:
    """Mapeia nomes de campos (Mongo/MySQL/ref_q*) para os nomes canônicos do schema."""
    alias_lookup = schema.alias_map()
    normalized: dict[str, Any] = {}
    extra_keys: list[str] = []
    duplicate_keys: list[str] = []

    for raw_key, raw_val in row.items():
        spec = alias_lookup.get(str(raw_key).lower())
        if spec is None:
            extra_keys.append(str(raw_key))
            continue
        if spec.canonical in normalized:
            duplicate_keys.append(str(raw_key))
            continue
        normalized[spec.canonical] = raw_val

    missing_keys = [f.canonical for f in schema.fields if f.canonical not in normalized]
    return normalized, missing_keys, extra_keys, duplicate_keys


def _canonical_key_value(
    field_spec: FieldSpec, raw_val: Any, strip_strings: bool = False
) -> Any:
    if field_spec.kind == "int":
        iv, err = _to_exact_int(raw_val)
        return iv if err is None else ("__raw__", str(raw_val))
    if field_spec.kind == "date":
        dv, err = _to_date_str(raw_val)
        return dv if err is None else ("__raw__", str(raw_val))
    if field_spec.kind == "str":
        v = unwrap_ejson(raw_val)
        if isinstance(v, str):
            return v.strip() if strip_strings else v
        return ("__raw__", str(v))
    dv, err = _to_decimal(raw_val)
    return dv if err is None else ("__raw__", str(raw_val))


def _row_key(
    schema: QuerySchema, norm_row: dict[str, Any], strip_strings: bool = False
) -> tuple[Any, ...] | None:
    parts: list[Any] = []
    for kf in schema.key_fields:
        if kf not in norm_row:
            return None
        fspec = schema.field_by_canonical(kf)
        parts.append(_canonical_key_value(fspec, norm_row[kf], strip_strings=strip_strings))
    return tuple(parts)


@dataclass
class FieldComparison:
    matched: bool
    exact: bool
    abs_err: Decimal | None = None
    rel_err: Decimal | None = None
    reason: str | None = None
    whitespace_only: bool = False


def compare_field(
    field_spec: FieldSpec,
    actual_raw: Any,
    expected_raw: Any,
    rel_tol: float = 1e-9,
    abs_tol: float = 1e-6,
    strict: bool = False,
    strip_strings: bool = False,
) -> FieldComparison:
    """Compara um valor real contra o valor esperado de acordo com o tipo do campo."""
    kind = field_spec.kind

    if kind == "str":
        act = unwrap_ejson(actual_raw)
        exp = unwrap_ejson(expected_raw)
        if not isinstance(act, str):
            return FieldComparison(
                False, False, reason=f"esperado str, recebido {type(act).__name__} ({act!r})"
            )
        if not isinstance(exp, str):
            return FieldComparison(
                False, False, reason=f"referência inválida (não str): {exp!r}"
            )
        if act == exp:
            return FieldComparison(True, True)
        if act.strip() == exp.strip():
            if strip_strings and not strict:
                return FieldComparison(True, False, whitespace_only=True)
            if act.rstrip() == exp.rstrip():
                reason = "diferença apenas em espaços à direita"
            elif act.lstrip() == exp.lstrip():
                reason = "diferença apenas em espaços à esquerda"
            else:
                reason = "diferença apenas em espaços nas extremidades (esquerda e direita)"
            return FieldComparison(False, False, reason=reason, whitespace_only=True)
        return FieldComparison(False, False, reason="strings diferentes")

    if kind == "int":
        act_int, act_err = _to_exact_int(actual_raw)
        if act_err is not None or act_int is None:
            return FieldComparison(False, False, reason=f"valor atual inválido: {act_err}")
        exp_int, exp_err = _to_exact_int(expected_raw)
        if exp_err is not None or exp_int is None:
            return FieldComparison(False, False, reason=f"valor de referência inválido: {exp_err}")
        diff = abs(Decimal(act_int - exp_int))
        rel = diff / abs(Decimal(exp_int)) if exp_int != 0 else diff
        if act_int == exp_int:
            return FieldComparison(True, True, abs_err=Decimal(0), rel_err=Decimal(0))
        return FieldComparison(
            False,
            False,
            abs_err=diff,
            rel_err=rel,
            reason=f"inteiro divergente (esperado {exp_int}, obtido {act_int})",
        )

    if kind == "date":
        act_date, act_err = _to_date_str(actual_raw)
        if act_err is not None or act_date is None:
            return FieldComparison(False, False, reason=f"data atual inválida: {act_err}")
        exp_date, exp_err = _to_date_str(expected_raw)
        if exp_err is not None or exp_date is None:
            return FieldComparison(False, False, reason=f"data de referência inválida: {exp_err}")
        if act_date == exp_date:
            return FieldComparison(True, True)
        return FieldComparison(
            False,
            False,
            reason=f"data divergente (esperado {exp_date}, obtido {act_date})",
        )

    # float ou avg
    act_dec, act_err = _to_decimal(actual_raw)
    if act_err is not None or act_dec is None:
        return FieldComparison(False, False, reason=f"número atual inválido: {act_err}")
    exp_dec, exp_err = _to_decimal(expected_raw)
    if exp_err is not None or exp_dec is None:
        return FieldComparison(False, False, reason=f"número de referência inválido: {exp_err}")

    abs_err = abs(act_dec - exp_dec)
    rel_err = abs_err / abs(exp_dec) if exp_dec != 0 else abs_err
    exact = abs_err == 0

    if strict:
        if exact:
            return FieldComparison(True, True, abs_err=abs_err, rel_err=rel_err)
        return FieldComparison(
            False,
            False,
            abs_err=abs_err,
            rel_err=rel_err,
            reason=f"modo --strict exige diferença zero (abs_err={abs_err})",
        )

    abs_tol_dec = Decimal(str(abs_tol))
    rel_tol_dec = Decimal(str(rel_tol))
    if abs_err <= abs_tol_dec or rel_err <= rel_tol_dec:
        return FieldComparison(True, exact, abs_err=abs_err, rel_err=rel_err)

    if kind == "avg":
        exp_exponent = exp_dec.as_tuple().exponent
        if isinstance(exp_exponent, int) and exp_exponent < 0:
            d = -exp_exponent
            quant_tol = Decimal("0.5") * (Decimal(10) ** (-d)) + Decimal("1e-12")
            if abs_err <= quant_tol:
                return FieldComparison(True, exact, abs_err=abs_err, rel_err=rel_err)

    return FieldComparison(
        False,
        False,
        abs_err=abs_err,
        rel_err=rel_err,
        reason=(
            f"divergência numérica acima da tolerância "
            f"(abs_err={abs_err:.6g}, rel_err={rel_err:.6g})"
        ),
    )


@dataclass
class ComparisonResult:
    passed: bool
    exact_match: bool
    row_count_actual: int
    row_count_expected: int
    max_abs_err: Decimal = Decimal(0)
    max_rel_err: Decimal = Decimal(0)
    max_err_field: str | None = None
    total_diffs: int = 0
    whitespace_only_diffs: int = 0
    whitespace_stripped_matches: int = 0
    diffs: list[dict[str, Any]] = field(default_factory=list)


def _format_scalar_for_diff(val: Any) -> Any:
    v = unwrap_ejson(val)
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    return v


def compare_results(
    schema: QuerySchema,
    actual_rows: list[dict[str, Any]],
    expected_rows: list[dict[str, Any]],
    rel_tol: float = 1e-9,
    abs_tol: float = 1e-6,
    strict: bool = False,
    max_diffs: int = 10,
    strip_strings: bool = False,
) -> ComparisonResult:
    """Compara lista de documentos reais com a lista de referência."""
    effective_strip = strip_strings and not strict
    result = ComparisonResult(
        passed=True,
        exact_match=True,
        row_count_actual=len(actual_rows),
        row_count_expected=len(expected_rows),
    )

    def add_diff(diff: dict[str, Any]) -> None:
        result.passed = False
        result.exact_match = False
        result.total_diffs += 1
        if max_diffs < 0 or len(result.diffs) < max_diffs:
            result.diffs.append(diff)

    if len(actual_rows) != len(expected_rows):
        add_diff(
            {
                "type": "row_count",
                "expected": len(expected_rows),
                "actual": len(actual_rows),
                "detail": (
                    f"quantidade de linhas divergente: esperado {len(expected_rows)}, "
                    f"obtido {len(actual_rows)}"
                ),
            }
        )

    norm_actual: list[dict[str, Any]] = []
    act_keys: list[tuple[Any, ...] | None] = []
    for idx, row in enumerate(actual_rows):
        if not isinstance(row, dict):
            add_diff(
                {
                    "type": "schema",
                    "side": "actual",
                    "row": idx,
                    "detail": f"linha {idx} não é objeto/dicionário ({type(row).__name__})",
                }
            )
            norm_actual.append({})
            act_keys.append(None)
            continue
        norm, missing, extra, dup = normalize_row(schema, row)
        if missing or extra or dup:
            parts: list[str] = []
            if missing:
                parts.append(f"campos ausentes: {missing}")
            if extra:
                parts.append(f"campos extras: {extra}")
            if dup:
                parts.append(f"campos duplicados: {dup}")
            add_diff(
                {
                    "type": "schema",
                    "side": "actual",
                    "row": idx,
                    "missing": missing,
                    "extra": extra,
                    "duplicate": dup,
                    "detail": f"linha {idx} (actual): " + "; ".join(parts),
                }
            )
        norm_actual.append(norm)
        act_keys.append(_row_key(schema, norm, strip_strings=effective_strip))

    norm_expected: list[dict[str, Any]] = []
    exp_keys: list[tuple[Any, ...] | None] = []
    for idx, row in enumerate(expected_rows):
        if not isinstance(row, dict):
            add_diff(
                {
                    "type": "schema",
                    "side": "expected",
                    "row": idx,
                    "detail": f"linha {idx} de referência não é objeto ({type(row).__name__})",
                }
            )
            norm_expected.append({})
            exp_keys.append(None)
            continue
        norm, missing, extra, dup = normalize_row(schema, row)
        if missing or extra or dup:
            parts = []
            if missing:
                parts.append(f"campos ausentes: {missing}")
            if extra:
                parts.append(f"campos extras: {extra}")
            if dup:
                parts.append(f"campos duplicados: {dup}")
            add_diff(
                {
                    "type": "schema",
                    "side": "expected",
                    "row": idx,
                    "missing": missing,
                    "extra": extra,
                    "duplicate": dup,
                    "detail": f"linha {idx} (referência): " + "; ".join(parts),
                }
            )
        norm_expected.append(norm)
        exp_keys.append(_row_key(schema, norm, strip_strings=effective_strip))

    # Verificação de chaves ausentes/extras (multiconjunto)
    act_key_counter = Counter(k for k in act_keys if k is not None)
    exp_key_counter = Counter(k for k in exp_keys if k is not None)

    for k, count in (exp_key_counter - act_key_counter).items():
        add_diff(
            {
                "type": "missing_row",
                "key": list(k),
                "count": count,
                "detail": f"linha ausente no resultado atual: chave={k} (x{count})",
            }
        )

    for k, count in (act_key_counter - exp_key_counter).items():
        add_diff(
            {
                "type": "extra_row",
                "key": list(k),
                "count": count,
                "detail": f"linha inesperada no resultado atual: chave={k} (x{count})",
            }
        )

    exp_positions: dict[tuple[Any, ...], list[int]] = defaultdict(list)
    for idx, k in enumerate(exp_keys):
        if k is not None:
            exp_positions[k].append(idx)

    def compare_pair(
        row_idx: int,
        row_key: tuple[Any, ...] | None,
        act_row: dict[str, Any],
        exp_row: dict[str, Any],
        expected_idx: int | None = None,
    ) -> None:
        for fspec in schema.fields:
            if fspec.canonical not in act_row or fspec.canonical not in exp_row:
                continue
            fc = compare_field(
                fspec,
                act_row[fspec.canonical],
                exp_row[fspec.canonical],
                rel_tol=rel_tol,
                abs_tol=abs_tol,
                strict=strict,
                strip_strings=strip_strings,
            )
            if not fc.exact:
                result.exact_match = False
            if fc.whitespace_only and fc.matched:
                result.whitespace_stripped_matches += 1
            if fc.abs_err is not None and fc.abs_err > result.max_abs_err:
                result.max_abs_err = fc.abs_err
                result.max_err_field = fspec.canonical
            if fc.rel_err is not None and fc.rel_err > result.max_rel_err:
                result.max_rel_err = fc.rel_err
                if result.max_err_field is None or fc.abs_err == result.max_abs_err:
                    result.max_err_field = fspec.canonical
            if not fc.matched:
                if fc.whitespace_only:
                    result.whitespace_only_diffs += 1
                diff_entry: dict[str, Any] = {
                    "type": "field_mismatch",
                    "row": row_idx,
                    "key": list(row_key) if row_key is not None else None,
                    "field": fspec.canonical,
                    "expected": _format_scalar_for_diff(exp_row[fspec.canonical]),
                    "actual": _format_scalar_for_diff(act_row[fspec.canonical]),
                    "detail": (
                        f"linha {row_idx}"
                        + (f" (chave={row_key})" if row_key is not None else "")
                        + f", campo '{fspec.canonical}': esperado={_format_scalar_for_diff(exp_row[fspec.canonical])!r}, "
                        f"obtido={_format_scalar_for_diff(act_row[fspec.canonical])!r}"
                        + (f" ({fc.reason})" if fc.reason else "")
                    ),
                }
                if fc.whitespace_only:
                    diff_entry["whitespace_only"] = True
                if expected_idx is not None and expected_idx != row_idx:
                    diff_entry["expected_row"] = expected_idx
                if fc.abs_err is not None:
                    diff_entry["abs_err"] = str(fc.abs_err)
                if fc.rel_err is not None:
                    diff_entry["rel_err"] = str(fc.rel_err)
                add_diff(diff_entry)

    # Comparação posicional (ordem determinística nas 3 queries TPC-H)
    for idx in range(min(len(norm_actual), len(norm_expected))):
        ak = act_keys[idx]
        ek = exp_keys[idx]
        if ak is not None and ek is not None and ak != ek:
            if ak in exp_positions:
                target_idx = exp_positions[ak][0]
                add_diff(
                    {
                        "type": "order_mismatch",
                        "row": idx,
                        "actual_key": list(ak),
                        "expected_key": list(ek),
                        "expected_positions_for_actual_key": exp_positions[ak],
                        "detail": (
                            f"ordem divergente na posição {idx}: obtido chave={ak} "
                            f"(esperada na posição {exp_positions[ak]}), esperado chave={ek}"
                        ),
                    }
                )
                compare_pair(idx, ak, norm_actual[idx], norm_expected[target_idx], target_idx)
            else:
                add_diff(
                    {
                        "type": "row_mismatch",
                        "row": idx,
                        "actual_key": list(ak),
                        "expected_key": list(ek),
                        "detail": (
                            f"linha {idx} divergente: chave obtida={ak} não consta na referência "
                            f"(esperado chave={ek})"
                        ),
                    }
                )
            continue

        compare_pair(idx, ak or ek, norm_actual[idx], norm_expected[idx], idx)

    return result


def load_json_rows(path: Path) -> list[dict[str, Any]]:
    """Carrega lista de documentos JSON (array JSON ou NDJSON)."""
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError(
                f"Arquivo {path} deve conter um array JSON de objetos (recebido {type(data).__name__})."
            )
        return data
    except json.JSONDecodeError as first_err:
        rows: list[dict[str, Any]] = []
        for line_no, line in enumerate(raw.splitlines(), start=1):
            s = line.strip()
            if not s:
                continue
            try:
                item = json.loads(s)
            except json.JSONDecodeError as line_err:
                raise ValueError(
                    f"JSON inválido em {path} (linha {line_no}): {first_err}"
                ) from line_err
            if not isinstance(item, dict):
                raise ValueError(f"Linha {line_no} em {path} não é um objeto JSON.")
            rows.append(item)
        return rows


def ref_file_path(query_id: int, ref_dir: Path = DEFAULT_REF_DIR) -> Path:
    return ref_dir / f"ref_q{query_id}.json"


def mysql_file_path(query_id: int, mysql_dir: Path = DEFAULT_MYSQL_DIR) -> Path:
    return mysql_dir / f"{query_id}.sql"


def _json_default(obj: Any) -> Any:
    if Decimal128 is not None and isinstance(obj, Decimal128):
        return str(obj.to_decimal())
    if Int64 is not None and isinstance(obj, Int64):
        return int(obj)
    if ObjectId is not None and isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Objeto não serializável em JSON: {type(obj).__name__}")


def save_json_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=_json_default) + "\n",
        encoding="utf-8",
    )


def format_rows_for_ref(schema: QuerySchema, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Formata linhas (ex.: do MySQL) no padrão exato dos arquivos ref_q*.json."""
    formatted: list[dict[str, Any]] = []
    for row in rows:
        norm, missing, extra, dup = normalize_row(schema, row)
        if missing or extra or dup:
            raise ValueError(
                f"Linha incompatível com schema Q{schema.query_id}: "
                f"missing={missing}, extra={extra}, dup={dup}"
            )
        out: dict[str, Any] = {}
        for fspec in schema.fields:
            val = unwrap_ejson(norm[fspec.canonical])
            if fspec.kind == "int":
                iv, err = _to_exact_int(val)
                if err is not None or iv is None:
                    raise ValueError(f"Campo {fspec.canonical} inválido para ref: {err}")
                out[fspec.ref_name] = iv
            elif fspec.kind == "date":
                dv, err = _to_date_str(val)
                if err is not None or dv is None:
                    raise ValueError(f"Campo {fspec.canonical} inválido para ref: {err}")
                out[fspec.ref_name] = dv
            elif fspec.kind in ("float", "avg"):
                dec, err = _to_decimal(val)
                if err is not None or dec is None:
                    raise ValueError(f"Campo {fspec.canonical} inválido para ref: {err}")
                out[fspec.ref_name] = str(dec)
            else:
                out[fspec.ref_name] = str(val)
        formatted.append(out)
    return formatted


def connect_mongo(uri: str, server_selection_timeout_ms: int = 5000) -> Any:
    try:
        from pymongo import MongoClient
    except ImportError as exc:
        raise RuntimeError(
            "Pacote 'pymongo' não encontrado. Execute usando .venv/bin/python."
        ) from exc

    client = MongoClient(
        uri,
        serverSelectionTimeoutMS=server_selection_timeout_ms,
        socketTimeoutMS=None,
    )
    client.admin.command("ping")
    return client


def connect_mysql(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
) -> Any:
    try:
        import mysql.connector
    except ImportError as exc:
        raise RuntimeError(
            "Pacote 'mysql-connector-python' não encontrado. Execute usando .venv/bin/python."
        ) from exc

    return mysql.connector.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
    )


def execute_mysql_query(conn: Any, sql_path: Path) -> tuple[list[dict[str, Any]], float]:
    sql_text = sql_path.read_text(encoding="utf-8").strip().rstrip(";").strip()
    t0 = time.perf_counter()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(sql_text)
        rows = cursor.fetchall()
    finally:
        cursor.close()
    elapsed = time.perf_counter() - t0
    return rows, elapsed


def execute_mongo_query(
    db: Any,
    spec: MongoQuerySpec,
    no_hint: bool = False,
    timeout_ms: int | None = None,
) -> tuple[list[dict[str, Any]], float]:
    options = dict(spec.options)
    if no_hint:
        options.pop("hint", None)
    if timeout_ms is not None:
        options["maxTimeMS"] = timeout_ms

    coll = db[spec.collection]
    t0 = time.perf_counter()
    with coll.aggregate(spec.pipeline, **options) as cursor:
        rows = list(cursor)
    elapsed = time.perf_counter() - t0
    return rows, elapsed


def _format_num_metric(val: Decimal) -> str:
    if val == 0:
        return "0"
    return f"{val:.3e}"


def _print_comparison_result(
    label: str,
    comp: ComparisonResult,
    elapsed_sec: float | None = None,
) -> None:
    status = "PASS" if comp.passed else "FAIL"
    timing = f" | tempo={elapsed_sec:.2f}s" if elapsed_sec is not None else ""
    err_info = (
        f" | max_abs_err={_format_num_metric(comp.max_abs_err)}, "
        f"max_rel_err={_format_num_metric(comp.max_rel_err)}"
        + (f" ({comp.max_err_field})" if comp.max_err_field else "")
    )
    strip_info = (
        f" | strip_strings={comp.whitespace_stripped_matches}"
        if comp.passed and comp.whitespace_stripped_matches > 0
        else ""
    )
    print(
        f"[{status}] {label} | linhas={comp.row_count_actual}/{comp.row_count_expected}"
        f"{timing}{err_info}{strip_info}"
    )
    if not comp.passed:
        for d in comp.diffs:
            print(f"       - {d['detail']}")
        omitted = comp.total_diffs - len(comp.diffs)
        if omitted > 0:
            print(f"       ... e mais {omitted} diferença(s) omitida(s) (--max-diffs).")
        if comp.total_diffs > 0 and comp.whitespace_only_diffs == comp.total_diffs:
            print(
                f"       [DICA] Todas as {comp.total_diffs} divergência(s) são apenas espaços "
                f"no início/fim das strings (comum quando ref_q*.json foi exportado com strip). "
                f"Use --strip-strings ou revalide contra o MySQL com --revalidate-mysql [--update-ref]."
            )


def run_check_db(args: argparse.Namespace) -> int:
    exit_code = 0
    if not args.mysql_only:
        print(f"Conectando ao MongoDB: {args.mongo_uri} (db={args.mongo_db})...")
        try:
            client = connect_mongo(args.mongo_uri)
        except Exception as exc:
            print(f"[ERRO] MongoDB inacessível: {exc}")
            exit_code = 1
        else:
            try:
                db = client[args.mongo_db]
                colls = sorted(db.list_collection_names())
                print(f"[OK] MongoDB acessível. Coleções em '{args.mongo_db}' ({len(colls)}):")
                for name in colls:
                    c = db[name]
                    est = c.estimated_document_count()
                    idxs = list(c.index_information().keys())
                    print(f"  - {name}: ~{est:,} docs | índices={idxs}")
            finally:
                client.close()

    if args.revalidate_mysql or args.mysql_only:
        print(
            f"Conectando ao MySQL: {args.mysql_user}@{args.mysql_host}:{args.mysql_port}/{args.mysql_db}..."
        )
        try:
            mysql_conn = connect_mysql(
                args.mysql_host,
                args.mysql_port,
                args.mysql_user,
                args.mysql_password,
                args.mysql_db,
            )
        except Exception as exc:
            print(f"[ERRO] MySQL inacessível: {exc}")
            exit_code = 1
        else:
            try:
                cur = mysql_conn.cursor()
                cur.execute("SHOW TABLES")
                tables = [row[0] for row in cur.fetchall()]
                cur.close()
                print(f"[OK] MySQL acessível. Tabelas ({len(tables)}): {tables}")
            finally:
                mysql_conn.close()

    return exit_code


def run_dry_run(
    specs: list[MongoQuerySpec],
    selected_queries: list[int],
    args: argparse.Namespace,
) -> int:
    print("=== DRY-RUN: Plano de Validação TPC-H ===")
    warnings: list[str] = []
    for s in specs:
        warnings.extend(s.warnings)

    if warnings:
        print("Avisos do parser:")
        for w in dict.fromkeys(warnings):
            print(f"  [AVISO] {w}")

    print("\nArquivos de referência MySQL:")
    exit_code = 0
    for qid in selected_queries:
        rpath = ref_file_path(qid)
        if not rpath.exists():
            print(f"  [ERRO] Q{qid}: arquivo ausente {rpath}")
            exit_code = 1
            continue
        try:
            rows = load_json_rows(rpath)
            schema = QUERY_SCHEMAS[qid]
            # Valida leitura/normalização das linhas de referência
            for idx, r in enumerate(rows):
                _, missing, extra, dup = normalize_row(schema, r)
                if missing or extra or dup:
                    raise ValueError(
                        f"linha {idx} com schema inesperado: missing={missing}, extra={extra}, dup={dup}"
                    )
            print(f"  [OK] Q{qid}: {rpath.name} ({len(rows)} linhas)")
        except Exception as exc:
            print(f"  [ERRO] Q{qid}: falha ao ler {rpath.name}: {exc}")
            exit_code = 1

    if not args.mysql_only:
        print(f"\nExecuções MongoDB planejadas ({len(specs)}):")
        for s in specs:
            opts = dict(s.options)
            if args.no_hint:
                opts.pop("hint", None)
            if args.timeout_ms is not None:
                opts["maxTimeMS"] = args.timeout_ms
            print(
                f"  - Q{s.query_id} | coleção={s.collection:<14} | "
                f"arquivo={s.source_file.name} (bloco {s.block_index}) | "
                f"estágios={len(s.pipeline)} | opções={opts}"
            )

    if args.revalidate_mysql or args.mysql_only:
        print("\nRevalidação MySQL planejada:")
        for qid in selected_queries:
            spath = mysql_file_path(qid)
            status = "OK" if spath.exists() else "AUSENTE"
            print(f"  - Q{qid}: {spath.name} [{status}]")

    return exit_code


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Valida respostas do MongoDB para as queries TPC-H anotadas em "
            "queries_tpch_mongodb/*.mongodb contra os resultados do MySQL (ref_q*.json)."
        )
    )
    parser.add_argument(
        "-q",
        "--query",
        type=int,
        nargs="+",
        help="IDs das queries TPC-H para validar (ex.: -q 2 ou -q 1 3). Padrão: todas.",
    )
    parser.add_argument(
        "-c",
        "--collection",
        nargs="+",
        help="Filtra coleções MongoDB específicas (ex.: -c orders parts).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Interpreta os arquivos .mongodb e verifica ref_q*.json sem conectar aos bancos.",
    )
    parser.add_argument(
        "--check-db",
        action="store_true",
        help="Testa conectividade com MongoDB (e MySQL se solicitado), lista coleções/índices e sai.",
    )
    parser.add_argument(
        "--no-hint",
        action="store_true",
        help="Remove a opção 'hint' dos pipelines antes de executar no MongoDB.",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=None,
        help="Define maxTimeMS para cada aggregate no MongoDB.",
    )
    parser.add_argument(
        "--rel-tol",
        type=float,
        default=1e-9,
        help="Tolerância relativa para campos float/avg (padrão: 1e-9).",
    )
    parser.add_argument(
        "--abs-tol",
        type=float,
        default=1e-6,
        help="Tolerância absoluta para campos float/avg (padrão: 1e-6).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exige igualdade numérica exata (tolerância zero).",
    )
    parser.add_argument(
        "--strip-strings",
        action="store_true",
        help="Ignora espaços no início/fim de strings (útil quando ref_q*.json foi exportado com strip; desativado com --strict).",
    )
    parser.add_argument(
        "--max-diffs",
        type=int,
        default=10,
        help="Número máximo de diferenças exibidas por checagem (padrão: 10).",
    )
    parser.add_argument(
        "--save-results",
        type=Path,
        default=None,
        help="Diretório onde salvar os resultados brutos do MongoDB em JSON.",
    )
    parser.add_argument(
        "--mongo-file",
        type=Path,
        default=None,
        help="Valida offline um arquivo JSON de resposta do MongoDB (exige único -q <id>).",
    )
    parser.add_argument(
        "--revalidate-mysql",
        action="store_true",
        help="Reexecuta queries_tpch_mysql/*.sql no MySQL, compara com ref_q*.json e usa como referência.",
    )
    parser.add_argument(
        "--mysql-only",
        action="store_true",
        help="Executa apenas queries_tpch_mysql/*.sql e valida contra ref_q*.json sem usar MongoDB.",
    )
    parser.add_argument(
        "--update-ref",
        action="store_true",
        help="Atualiza ref_q*.json com o resultado vivo do MySQL (requer --revalidate-mysql ou --mysql-only).",
    )
    parser.add_argument(
        "--report-json",
        type=Path,
        default=None,
        help="Salva relatório completo da validação em JSON.",
    )
    parser.add_argument("--mongo-uri", default=DEFAULT_MONGO_URI, help="URI do MongoDB.")
    parser.add_argument("--mongo-db", default=DEFAULT_MONGO_DB, help="Nome do banco no MongoDB.")
    parser.add_argument("--mysql-host", default=DEFAULT_MYSQL_HOST, help="Host do MySQL.")
    parser.add_argument("--mysql-port", type=int, default=DEFAULT_MYSQL_PORT, help="Porta do MySQL.")
    parser.add_argument("--mysql-user", default=DEFAULT_MYSQL_USER, help="Usuário do MySQL.")
    parser.add_argument("--mysql-password", default=DEFAULT_MYSQL_PASSWORD, help="Senha do MySQL.")
    parser.add_argument("--mysql-db", default=DEFAULT_MYSQL_DB, help="Banco do MySQL.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.update_ref and not (args.revalidate_mysql or args.mysql_only):
        parser.error("--update-ref requer --revalidate-mysql ou --mysql-only.")

    if args.mongo_file is not None:
        if not args.query or len(args.query) != 1:
            parser.error("--mongo-file exige exatamente um ID em -q / --query (ex.: -q 1).")
        if args.mysql_only:
            parser.error("--mongo-file não pode ser combinado com --mysql-only.")

    try:
        all_specs = discover_mongo_queries()
    except (FileNotFoundError, MongoParseError) as exc:
        print(f"[ERRO] Falha ao interpretar queries MongoDB: {exc}", file=sys.stderr)
        return 1

    available_qids = sorted({s.query_id for s in all_specs} | set(QUERY_SCHEMAS.keys()))
    if args.query:
        unknown = [q for q in args.query if q not in QUERY_SCHEMAS]
        if unknown:
            print(f"[ERRO] Queries sem schema conhecido: {unknown}", file=sys.stderr)
            return 1
        selected_qids = list(dict.fromkeys(args.query))
    else:
        selected_qids = sorted({s.query_id for s in all_specs})

    filtered_specs = [s for s in all_specs if s.query_id in selected_qids]
    if args.collection:
        wanted_colls = set(args.collection)
        filtered_specs = [s for s in filtered_specs if s.collection in wanted_colls]

    if not args.mysql_only and args.mongo_file is None and not filtered_specs and not args.check_db:
        print(
            "[ERRO] Nenhuma execução MongoDB corresponde aos filtros (-q / -c) informados.",
            file=sys.stderr,
        )
        return 1

    if args.check_db:
        return run_check_db(args)

    if args.dry_run:
        return run_dry_run(filtered_specs, selected_qids, args)

    # Exibe eventuais avisos de parsing antes das execuções
    seen_warnings: list[str] = []
    for s in filtered_specs:
        for w in s.warnings:
            if w not in seen_warnings:
                seen_warnings.append(w)
                print(f"[AVISO] {w}")

    report_checks: list[dict[str, Any]] = []
    has_failure = False

    # 1. Carrega referências iniciais (ref_q*.json) ou revalida contra MySQL ao vivo
    references: dict[int, list[dict[str, Any]]] = {}
    mysql_conn = None
    try:
        if args.revalidate_mysql or args.mysql_only:
            try:
                mysql_conn = connect_mysql(
                    args.mysql_host,
                    args.mysql_port,
                    args.mysql_user,
                    args.mysql_password,
                    args.mysql_db,
                )
            except Exception as exc:
                print(f"[ERRO] Falha ao conectar ao MySQL: {exc}", file=sys.stderr)
                return 1

            for qid in selected_qids:
                schema = QUERY_SCHEMAS[qid]
                spath = mysql_file_path(qid)
                rpath = ref_file_path(qid)
                label = f"Q{qid} MySQL ({spath.name}) vs {rpath.name}"
                try:
                    live_rows, elapsed = execute_mysql_query(mysql_conn, spath)
                    if rpath.exists():
                        ref_rows = load_json_rows(rpath)
                        comp = compare_results(
                            schema,
                            live_rows,
                            ref_rows,
                            rel_tol=args.rel_tol,
                            abs_tol=args.abs_tol,
                            strict=args.strict,
                            max_diffs=args.max_diffs,
                            strip_strings=args.strip_strings,
                        )
                        _print_comparison_result(label, comp, elapsed_sec=elapsed)
                        if not comp.passed:
                            has_failure = True
                        report_checks.append(
                            {
                                "check": label,
                                "kind": "mysql_vs_ref",
                                "query_id": qid,
                                "passed": comp.passed,
                                "exact_match": comp.exact_match,
                                "elapsed_sec": round(elapsed, 4),
                                "row_count_actual": comp.row_count_actual,
                                "row_count_expected": comp.row_count_expected,
                                "max_abs_err": str(comp.max_abs_err),
                                "max_rel_err": str(comp.max_rel_err),
                                "max_err_field": comp.max_err_field,
                                "total_diffs": comp.total_diffs,
                                "diffs": comp.diffs,
                            }
                        )
                    else:
                        print(
                            f"[INFO] {label}: {rpath.name} inexistente; "
                            f"usando {len(live_rows)} linhas do MySQL ao vivo."
                        )

                    if args.update_ref:
                        formatted = format_rows_for_ref(schema, live_rows)
                        save_json_file(rpath, formatted)
                        print(f"[INFO] Referência atualizada: {rpath}")
                        references[qid] = formatted
                    else:
                        references[qid] = live_rows
                except Exception as exc:
                    has_failure = True
                    print(f"[ERRO] {label}: {exc}")
                    report_checks.append(
                        {
                            "check": label,
                            "kind": "mysql_vs_ref",
                            "query_id": qid,
                            "passed": False,
                            "error": str(exc),
                        }
                    )
        else:
            for qid in selected_qids:
                rpath = ref_file_path(qid)
                try:
                    references[qid] = load_json_rows(rpath)
                except Exception as exc:
                    has_failure = True
                    print(f"[ERRO] Falha ao carregar {rpath}: {exc}")
                    report_checks.append(
                        {
                            "check": f"Q{qid} load {rpath.name}",
                            "kind": "load_ref",
                            "query_id": qid,
                            "passed": False,
                            "error": str(exc),
                        }
                    )
    finally:
        if mysql_conn is not None:
            mysql_conn.close()

    # 2. Validação offline (--mongo-file) ou ao vivo no MongoDB
    if args.mongo_file is not None:
        qid = selected_qids[0]
        schema = QUERY_SCHEMAS[qid]
        label = f"Q{qid} arquivo {args.mongo_file.name}"
        if qid not in references:
            has_failure = True
            print(f"[ERRO] Sem referência disponível para Q{qid}.")
        else:
            try:
                mongo_rows = load_json_rows(args.mongo_file)
                comp = compare_results(
                    schema,
                    mongo_rows,
                    references[qid],
                    rel_tol=args.rel_tol,
                    abs_tol=args.abs_tol,
                    strict=args.strict,
                    max_diffs=args.max_diffs,
                    strip_strings=args.strip_strings,
                )
                _print_comparison_result(label, comp)
                if not comp.passed:
                    has_failure = True
                report_checks.append(
                    {
                        "check": label,
                        "kind": "mongo_file",
                        "query_id": qid,
                        "passed": comp.passed,
                        "exact_match": comp.exact_match,
                        "row_count_actual": comp.row_count_actual,
                        "row_count_expected": comp.row_count_expected,
                        "max_abs_err": str(comp.max_abs_err),
                        "max_rel_err": str(comp.max_rel_err),
                        "max_err_field": comp.max_err_field,
                        "total_diffs": comp.total_diffs,
                        "diffs": comp.diffs,
                    }
                )
            except Exception as exc:
                has_failure = True
                print(f"[ERRO] {label}: {exc}")
                report_checks.append(
                    {
                        "check": label,
                        "kind": "mongo_file",
                        "query_id": qid,
                        "passed": False,
                        "error": str(exc),
                    }
                )
    elif not args.mysql_only:
        mongo_client = None
        try:
            mongo_client = connect_mongo(args.mongo_uri)
        except Exception as exc:
            has_failure = True
            print(f"[ERRO] Falha ao conectar ao MongoDB ({args.mongo_uri}): {exc}", file=sys.stderr)
            report_checks.append(
                {
                    "check": "MongoDB connection",
                    "kind": "mongo_connection",
                    "passed": False,
                    "error": str(exc),
                }
            )
        else:
            try:
                mongo_db = mongo_client[args.mongo_db]
                for spec in filtered_specs:
                    qid = spec.query_id
                    schema = QUERY_SCHEMAS[qid]
                    label = (
                        f"Q{qid} MongoDB ({spec.collection}, "
                        f"{spec.source_file.name}#bloco{spec.block_index})"
                    )
                    if qid not in references:
                        has_failure = True
                        print(f"[ERRO] {label}: sem referência disponível para Q{qid}.")
                        continue

                    try:
                        mongo_rows, elapsed = execute_mongo_query(
                            mongo_db,
                            spec,
                            no_hint=args.no_hint,
                            timeout_ms=args.timeout_ms,
                        )
                        if args.save_results is not None:
                            out_path = args.save_results / f"mongo_q{qid}_{spec.collection}.json"
                            save_json_file(out_path, mongo_rows)

                        comp = compare_results(
                            schema,
                            mongo_rows,
                            references[qid],
                            rel_tol=args.rel_tol,
                            abs_tol=args.abs_tol,
                            strict=args.strict,
                            max_diffs=args.max_diffs,
                            strip_strings=args.strip_strings,
                        )
                        _print_comparison_result(label, comp, elapsed_sec=elapsed)
                        if not comp.passed:
                            has_failure = True
                        report_checks.append(
                            {
                                "check": label,
                                "kind": "mongo_vs_ref",
                                "query_id": qid,
                                "collection": spec.collection,
                                "block_index": spec.block_index,
                                "passed": comp.passed,
                                "exact_match": comp.exact_match,
                                "elapsed_sec": round(elapsed, 4),
                                "row_count_actual": comp.row_count_actual,
                                "row_count_expected": comp.row_count_expected,
                                "max_abs_err": str(comp.max_abs_err),
                                "max_rel_err": str(comp.max_rel_err),
                                "max_err_field": comp.max_err_field,
                                "total_diffs": comp.total_diffs,
                                "diffs": comp.diffs,
                            }
                        )
                    except Exception as exc:
                        has_failure = True
                        print(f"[ERRO] {label}: {exc}")
                        report_checks.append(
                            {
                                "check": label,
                                "kind": "mongo_vs_ref",
                                "query_id": qid,
                                "collection": spec.collection,
                                "block_index": spec.block_index,
                                "passed": False,
                                "error": str(exc),
                            }
                        )
            finally:
                mongo_client.close()

    # 3. Resumo final
    total = len(report_checks)
    passed_count = sum(1 for c in report_checks if c.get("passed"))
    failed_count = total - passed_count
    print("\n=== Resumo da Validação ===")
    print(f"Total de checagens: {total} | Aprovadas: {passed_count} | Falhas/Erros: {failed_count}")
    for c in report_checks:
        tag = "PASS" if c.get("passed") else ("ERRO" if "error" in c else "FAIL")
        extra = (
            f"erro={c['error']}"
            if "error" in c
            else f"linhas={c.get('row_count_actual')}/{c.get('row_count_expected')}, "
            f"max_rel_err={c.get('max_rel_err')}"
        )
        print(f"  [{tag}] {c['check']} ({extra})")

    if args.report_json is not None:
        report_payload = {
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "config": {
                "queries": selected_qids,
                "collections": args.collection,
                "rel_tol": args.rel_tol,
                "abs_tol": args.abs_tol,
                "strict": args.strict,
                "strip_strings": args.strip_strings,
                "no_hint": args.no_hint,
                "timeout_ms": args.timeout_ms,
                "revalidate_mysql": args.revalidate_mysql,
                "mysql_only": args.mysql_only,
            },
            "warnings": seen_warnings,
            "summary": {
                "total_checks": total,
                "passed_checks": passed_count,
                "failed_checks": failed_count,
                "all_passed": not has_failure and total > 0,
            },
            "checks": report_checks,
        }
        save_json_file(args.report_json, report_payload)
        print(f"Relatório salvo em: {args.report_json}")

    if has_failure or total == 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
