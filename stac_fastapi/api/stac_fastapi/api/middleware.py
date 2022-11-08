"""api middleware."""
import datetime
import json
import re
import typing
from http.client import HTTP_PORT, HTTPS_PORT
from typing import List, Tuple

import requests
from starlette.datastructures import MutableHeaders
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware as _CORSMiddleware
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from .azure import get_read_sas_token
import logging
logger = logging.getLogger("uvicorn")


class Token:
    def __init__(self):
        pass

    token: str = ""
    token_expire: datetime.datetime = None
    collection = None


token_store = {}


class CORSMiddleware(_CORSMiddleware):
    """
    Subclass of Starlette's standard CORS middleware with default values set to those reccomended by the STAC API spec.

    https://github.com/radiantearth/stac-api-spec/blob/914cf8108302e2ec734340080a45aaae4859bb63/implementation.md#cors
    """

    def __init__(
            self,
            app: ASGIApp,
            allow_origins: typing.Sequence[str] = ("*",),
            allow_methods: typing.Sequence[str] = (
                    "OPTIONS",
                    "POST",
                    "GET",
            ),
            allow_headers: typing.Sequence[str] = ("Content-Type",),
            allow_credentials: bool = False,
            allow_origin_regex: typing.Optional[str] = None,
            expose_headers: typing.Sequence[str] = (),
            max_age: int = 600,
    ) -> None:
        """Create CORS middleware."""
        super().__init__(
            app,
            allow_origins,
            allow_methods,
            allow_headers,
            allow_credentials,
            allow_origin_regex,
            expose_headers,
            max_age,
        )


class ProxyHeaderMiddleware:
    """
    Account for forwarding headers when deriving base URL.

    Prioritise standard Forwarded header, look for non-standard X-Forwarded-* if missing.
    Default to what can be derived from the URL if no headers provided.
    Middleware updates the host header that is interpreted by starlette when deriving Request.base_url.
    """

    def __init__(self, app: ASGIApp):
        """Create proxy header middleware."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Call from stac-fastapi framework."""
        if scope["type"] == "http":
            proto, domain, port = self._get_forwarded_url_parts(scope)
            scope["scheme"] = proto
            if domain is not None:
                port_suffix = ""
                if port is not None:
                    if (proto == "http" and port != HTTP_PORT) or (
                            proto == "https" and port != HTTPS_PORT
                    ):
                        port_suffix = f":{port}"
                scope["headers"] = self._replace_header_value_by_name(
                    scope,
                    "host",
                    f"{domain}{port_suffix}",
                )
        await self.app(scope, receive, send)

    def _get_forwarded_url_parts(self, scope: Scope) -> Tuple[str]:
        proto = scope.get("scheme", "http")
        header_host = self._get_header_value_by_name(scope, "host")
        if header_host is None:
            domain, port = scope.get("server")
        else:
            header_host_parts = header_host.split(":")
            if len(header_host_parts) == 2:
                domain, port = header_host_parts
            else:
                domain = header_host_parts[0]
                port = None
        forwarded = self._get_header_value_by_name(scope, "forwarded")
        if forwarded is not None:
            parts = forwarded.split(";")
            for part in parts:
                if len(part) > 0 and re.search("=", part):
                    key, value = part.split("=")
                    if key == "proto":
                        proto = value
                    elif key == "host":
                        host_parts = value.split(":")
                        domain = host_parts[0]
                        try:
                            port = int(host_parts[1]) if len(host_parts) == 2 else None
                        except ValueError:
                            # ignore ports that are not valid integers
                            pass
        else:
            proto = self._get_header_value_by_name(scope, "x-forwarded-proto", proto)
            port_str = self._get_header_value_by_name(scope, "x-forwarded-port", port)
            try:
                port = int(port_str) if port_str is not None else None
            except ValueError:
                # ignore ports that are not valid integers
                pass

        return (proto, domain, port)

    def _get_header_value_by_name(
            self, scope: Scope, header_name: str, default_value: str = None
    ) -> str:
        headers = scope["headers"]
        candidates = [
            value.decode() for key, value in headers if key.decode() == header_name
        ]
        return candidates[0] if len(candidates) == 1 else default_value

    @staticmethod
    def _replace_header_value_by_name(
            scope: Scope, header_name: str, new_value: str
    ) -> List[Tuple[str]]:
        return [
                   (name, value)
                   for name, value in scope["headers"]
                   if name.decode() != header_name
               ] + [(str.encode(header_name), str.encode(new_value))]


class EncodingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        new_header = MutableHeaders(request.headers)
        new_header["Accept-Encoding"] = "utf-8"
        request.scope.update(headers=new_header.raw)
        response = await call_next(request)
        return response


class BlobAccessMiddleware(BaseHTTPMiddleware):

    async def dispatch(
            self,
            request,
            handler,
    ) -> Response:
        response = await handler(request)
        intercept_paths = [
            "search",
            "items",
        ]
        if 300 <= response.status_code < 400:
            return response
        if not list(set(request.url.path.split("/")).intersection(set(intercept_paths))):
            return response
        original_response_type = response.headers.get("Content-Type")
        binary = b''
        async for data in response.body_iterator:
            binary += data
        # try decoding with all encodings and return the first one that works
        decoded = binary.decode()
        decoded = json.loads(decoded)

        try:
            for item in decoded['features']:
                for asset in item['assets'].values():
                    _, asset['href'] = get_read_sas_token(asset['href'])
            return JSONResponse(content=decoded, status_code=response.status_code, media_type=original_response_type)
        except KeyError:
            pass

        try:
            asset_values = decoded['assets'].values()
            # replace all hrefs with the same value
            for asset in asset_values:
                _, asset['href'] = get_read_sas_token(asset['href'])
            return JSONResponse(content=decoded, status_code=response.status_code, media_type=original_response_type)
        except KeyError:
            pass

        return JSONResponse(content=decoded, status_code=response.status_code, media_type=original_response_type)


class MicrosoftPlanetaryComputerMiddleware(BaseHTTPMiddleware):
    async def dispatch(
            self,
            request,
            handler,
    ) -> Response:
        response = await handler(request)
        intercept_paths = [
            "search",
            "items",
        ]
        if 300 <= response.status_code < 400:
            return response
        if not list(set(request.url.path.split("/")).intersection(set(intercept_paths))):
            return response
        # if response code is in 300 to 399 range, then it is a redirect, bypass it
        original_response_type = response.headers.get("Content-Type")
        binary = b''
        async for data in response.body_iterator:
            binary += data
        # try decoding with all encodings and return the first one that works
        decoded = binary.decode()
        decoded = json.loads(decoded)

        def get_token_from_microsoft_blob(collection_name, storage_account, blob_name):
            url = f'https://planetarycomputer.microsoft.com/api/sas/v1/token/{storage_account}/{blob_name}'
            try:
                r = requests.get(url)
                a: Token = Token()
                a.token = r.json()['token']
                a.collection_name = collection_name
                a.token_expire = datetime.datetime.strptime(r.json()['msft:expiry'], '%Y-%m-%dT%H:%M:%SZ')
                token_store[collection_name] = a
                return a
            except:
                logging.info(f"This collection {collection_name} is not available on Microsoft Planetary Computer")
                token_store[collection_name] = None
                return None

        def get_token(collection_name, storage_account, blob_name):
            if collection_name in token_store:
                token = token_store[collection_name]
                if token is None:
                    logging.info("Token for this blob does not exist")
                    return None
                if token.token_expire - datetime.datetime.now() < datetime.timedelta(minutes=10):
                    logging.info("Refreshing token")
                    return get_token_from_microsoft_blob(collection_name, storage_account, blob_name)
                else:
                    logging.info("Using cached token")
                    return token
            else:
                logging.info("Getting token for the first time")
                return get_token_from_microsoft_blob(collection_name, storage_account, blob_name)

        def tokenify(stac_body):
            av = stac_body['assets'].values()
            collection_id = [link['href'] for link in stac_body['links'] if link['rel'] == 'collection'][0].split('/')[
                -1]
            for a in av:
                asset_href = a['href']
                try:
                    storage_account_name = asset_href.split('https://')[1].split('.blob.core.windows.net/')[0]
                    blob_name = asset_href.split('.blob.core.windows.net/')[1].split('/')[0]
                    token = get_token(collection_id, storage_account_name, blob_name)
                    if token is not None:
                        a['href'] = f'{asset_href}?{token.token}'
                except (IndexError, KeyError):
                    pass

            return av

        try:
            for item in decoded['features']:
                tokenify(item)
            return JSONResponse(content=decoded, status_code=response.status_code, media_type=original_response_type)
        except KeyError:
            pass
        except Exception as e:
            pass
        try:
            # find the collection id from the links where rel is collection
            tokenify(decoded)
            return JSONResponse(content=decoded, status_code=response.status_code, media_type=original_response_type)
        except KeyError:
            pass
        except Exception as e:
            pass
        return JSONResponse(content=decoded, status_code=response.status_code, media_type=original_response_type)
