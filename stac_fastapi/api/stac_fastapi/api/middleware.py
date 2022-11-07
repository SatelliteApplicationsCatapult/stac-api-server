"""api middleware."""
import datetime
import json
import re
import typing
from datetime import timedelta
from http.client import HTTP_PORT, HTTPS_PORT
from typing import List, Tuple

import requests
from starlette.datastructures import MutableHeaders
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware as _CORSMiddleware
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from .azure import get_read_sas_token

tokens = {}
tokens_expiry_timestamps = {}
collections_from_mpc = {}


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
            return JSONResponse(content=decoded, status_code=response.status_code)
        except KeyError:
            pass

        try:
            asset_values = decoded['assets'].values()
            # replace all hrefs with the same value
            for asset in asset_values:
                _, asset['href'] = get_read_sas_token(asset['href'])
            return JSONResponse(content=decoded, status_code=response.status_code)
        except KeyError:
            pass

        return JSONResponse(content=decoded, status_code=response.status_code)


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

        binary = b''
        async for data in response.body_iterator:
            binary += data
        # try decoding with all encodings and return the first one that works
        decoded = binary.decode()
        decoded = json.loads(decoded)

        def is_collection_from_mpc(a):
            if a in collections_from_mpc:
                return collections_from_mpc[a]
            else:
                url = f'https://planetarycomputer.microsoft.com/api/stac/v1/collections/{a}'
                try:
                    r = requests.get(url)
                    collections_from_mpc[a] = r.status_code == 200
                    if a not in tokens:
                        tokens[a] = get_sas_token_from_microsoft(a)
                    return collections_from_mpc[a]
                except:
                    collections_from_mpc[a] = False
                    return False

        def get_sas_token_from_microsoft(a):
            if a in tokens:
                token_expiry_time = tokens_expiry_timestamps[a]
                token_expiry_time = datetime.datetime.strptime(token_expiry_time, "%Y-%m-%dT%H:%M:%SZ")
                if token_expiry_time - datetime.datetime.now() < timedelta(minutes=20):
                    del tokens[a]
                    del tokens_expiry_timestamps[a]
                    return get_sas_token_from_microsoft(a)
                return tokens[a]
            else:
                try:
                    url = f"https://planetarycomputer.microsoft.com/api/sas/v1/token/{a}"
                    rsp = requests.get(url)
                    token = rsp.json()['token']
                    expiry_time = rsp.json()['msft:expiry']
                    tokens[a] = token
                    tokens_expiry_timestamps[a] = expiry_time
                    return token
                except:
                    tokens[a] = None

        def tokenify(stac_body):
            av = stac_body['assets'].values()
            collection_id = [link['href'] for link in stac_body['links'] if link['rel'] == 'collection'][0].split('/')[
                -1]
            if is_collection_from_mpc(collection_id):
                token = get_sas_token_from_microsoft(collection_id)
                for a in av:
                    asset_href = a['href']
                    # if asset_href does not have a token, add it
                    if '?' not in asset_href and token is not None:
                        a['href'] = f"{asset_href}?{token}"
            return av

        try:
            for item in decoded['features']:
                tokenify(item)
            return JSONResponse(content=decoded, status_code=response.status_code)
        except KeyError:
            pass
        except Exception as e:
            pass
        try:
            # find the collection id from the links where rel is collection
            tokenify(decoded)
            return JSONResponse(content=decoded, status_code=response.status_code)
        except KeyError:
            pass
        except Exception as e:
            pass
        return JSONResponse(content=decoded, status_code=response.status_code)
