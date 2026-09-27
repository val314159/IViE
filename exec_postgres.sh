docker exec -it \
       -e PGOPTIONS='-c search_path=grid,public' \
       ivie_pg psql -U ivie_user -d ivie_db
