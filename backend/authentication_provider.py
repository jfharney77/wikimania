import base64
import math
import os
import time
import uuid
from typing import Iterator


import httpx

# FIXME: This is not the auth to be used!!! This is just a placeholder to demonstrate how to use the auth package to get tokens and add them to headers. The actual implementation will depend on the authentication server and the token endpoint.
from backend import auth


class AuthenticationProvider:
    def __init__(self) -> None:
        self.client_id = os.getenv("CLIENT_ID", "")
        self.client_secret = os.getenv("CLIENT_SECRET", "")

    def generate_auth_token(self):
        """Generates an authentication token."""
        return self._get_bearer_token()

    def get_basic_credentials(self) -> str:
        self._validate_client_credentials()

        raw = f"{self.client_id}:{self.client_secret}".encode("utf-8")
        return base64.b64encode(raw).decode("utf-8")

    def _get_bearer_token(self):
        """Generates an authentication token using the client credentials flow."""
        self._validate_client_credentials()

        # Placeholder implementation for generating a bearer token using client credentials.
        # In a real implementation, this would involve making a request to the authentication server's token endpoint.
        return auth.client_credentials.get_token(self.client_id, self.client_secret).token

  

    def _validate_client_credentials(self) -> None:
        if self.client_id == "Insert your client id here" or self.client_id is None or self.client_secret == "Insert your client secret here" or self.client_secret is None:
            print ("*** Please set your client_id and client_secret in the .env file or set Use_SSO to true before running the application. ***")
            raise Exception("Invalid client credentials")



class AuthenticationProviderWithClientSideTokenRefresh(httpx.Auth):
    def __init__(self) -> None:
        """Initialize the AuthenticationProviderWithClientSideTokenRefresh class."""
        # Below properties are applicable OAUTH only
        self.client_id = os.getenv("CLIENT_ID", "")
        self.client_secret = os.getenv("CLIENT_SECRET", "")
        self.last_refreshed = math.floor(time.time())
        self.valid_until = math.floor(time.time()) - 1  # Set to past to force refresh on first use
        

    def auth_flow(self, request: httpx.Request) -> Iterator[httpx.Request]:
        """Authenticates a reqest using Client and Secret.
        
        """
        if "x-correlation-id" not in request.headers:
            request.headers["x-correlation-id"] = str(uuid.uuid4())
        request.headers["Authorization"] = f"Bearer {self._get_bearer_token()}"
        yield request
        
    def get_bearer_token(self):
        """Returns the bearer token, refreshing it if necessary."""
        if self._is_expired():
            print ("Generating new token...")
            self.last_refreshed = math.floor(time.time())
            _resp = auth.client_credentials(self.client_id, self.client_secret)
            self.token = _resp.token
            self.expires_in = _resp.expires_in
            self.valid_until = self.last_refreshed + self.expires_in
        else:
            print ("Token not expired, using existing token...")
        return self.token
       
    def _is_expired(self) -> bool:
        """Checks if the current token is expired."""
        # Consider token expired if it's within 60 seconds of expiring to account for clock skew
        return time.time() >= self.valid_until