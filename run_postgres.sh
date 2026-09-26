docker run -d \
       --name ivie_pg \
       -e POSTGRES_PASSWORD=ivie_secret \
       -e POSTGRES_DB=ivie_db \
       -e POSTGRES_USER=ivie_user \
       -v $PWD/secret:/tmp/secret \
       -v ivie_pg_data:/var/lib/postgresql/data \
       -p 5432:5432 \
       postgres:16
