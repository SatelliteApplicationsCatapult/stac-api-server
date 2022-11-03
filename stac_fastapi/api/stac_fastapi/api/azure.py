import os
from datetime import datetime, timedelta

from azure.storage.blob import generate_blob_sas, BlobSasPermissions

AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
AZURE_STORAGE_BLOB_NAME_FOR_STAC_ITEMS = os.getenv("AZURE_STORAGE_BLOB_NAME_FOR_STAC_ITEMS", "stac-items")


def get_read_sas_token(filename: str):
    connection_string = AZURE_STORAGE_CONNECTION_STRING
    account_key = connection_string.split("AccountKey=")[1].split(";")[0]
    connection_string_split = connection_string.split(";")
    azure_params = {}
    for param in connection_string_split:
        param_split = param.split("=")
        azure_params[param_split[0]] = param_split[1]
    azure_params["AccountKey"] = account_key
    account_name = azure_params["AccountName"]
    account_key = azure_params["AccountKey"]
    endpoint_suffix = azure_params["EndpointSuffix"]

    if account_name not in filename:
        return "", filename

    if filename.startswith("http"):
        filename = filename.split("/")[-1]

    container_name = AZURE_STORAGE_BLOB_NAME_FOR_STAC_ITEMS
    # create read sas token
    sas_token = generate_blob_sas(
        account_name=account_name,
        account_key=account_key,
        container_name=container_name,
        blob_name=filename,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.utcnow() + timedelta(hours=1),
    )
    return sas_token, f"https://{account_name}.blob.{endpoint_suffix}/{container_name}/{filename}?{sas_token}"
