# 1. Start the 3 containers
docker compose up -d

# 2. Initialize the Replica Set on port 27021
docker exec -it mongo1 mongosh --port 27021 --eval '
rs.initiate({
  _id: "rs0",
  members: [
    { _id: 0, host: "localhost:27021", priority: 2 },
    { _id: 1, host: "localhost:27022", priority: 1 },
    { _id: 2, host: "localhost:27023", priority: 1 }
  ]
})'