# 1. Criar os diretórios de dados e logs para as 3 réplicas
mkdir -p ~/mongo-cluster/{r1,r2,r3}

# 2. Iniciar as 3 instâncias em portas distintas (27017, 27018, 27019)
mongod --replSet rs0 --port 27017 --bind_ip localhost --dbpath ~/mongo-cluster/r1 --logpath ~/mongo-cluster/r1/mongod.log --fork
mongod --replSet rs0 --port 27018 --bind_ip localhost --dbpath ~/mongo-cluster/r2 --logpath ~/mongo-cluster/r2/mongod.log --fork
mongod --replSet rs0 --port 27019 --bind_ip localhost --dbpath ~/mongo-cluster/r3 --logpath ~/mongo-cluster/r3/mongod.log --fork

# 3. Conectar na primeira instância e inicializar o Replica Set
mongosh --port 27017 --eval '
rs.initiate({
  _id: "rs0",
  members: [
    { _id: 0, host: "localhost:27017" },
    { _id: 1, host: "localhost:27018" },
    { _id: 2, host: "localhost:27019" }
  ]
})'