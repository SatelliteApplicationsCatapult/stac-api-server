#!/bin/bash
if [[ ${ENVIRONMENT} == "Prod" ]]
then
    db_admin_username=$POSTGRES_USER
    db_admin_password=$POSTGRES_PASS
    db_host=$POSTGRES_HOST_WRITER
    script_path=$(dirname $(readlink -f $0))
    PGPASSWORD=${db_admin_password} psql -h $db_host -U $db_admin_username -d pgstac -f ${script_path}/setup_pgstac_schema.sql
else
    echo "Development"
fi

