import io
import logging
import os
import uuid
import zipfile

import certifi
import httpx
import requests

CREDENTIALS_ZIP_FILE_URL = "https://example.com/path/to/credentials.zip"  # Replace with the actual URL to the credentials zip file``
ROOT_CERT_NAME = "root_cert.pem"  # Replace with the actual root certificate name within the zip file``
ISSUING_CERT_NAME = "issuing_cert.pem"  # Replace with the actual issuing certificate name within the zip file``
CERT_BUNDLE_NAME = "custom_cert_bundle.pem"  # Name for the combined certificate bundle that will be created locally

logger = logging.getLogger("llm_utils")


def set_custom_cert_path(path) -> None:
    """Set the custome certificate path and override certifi.where() t"""
    global _custom_cert_path
    _custom_cert_path = path

    # Override certifi.where to return the custom certificate path
    def override_certifi_where():
        if _custom_cert_path and os.path.exists(_custom_cert_path):
            return _custom_cert_path
        return original_certifi_where()

    #Monkey patch certifi.where
    global original_certifi_where
    original_certifi_where = certifi.where
    certifi.where = override_certifi_where



def validate_client_credentials(client_id, client_secret) -> None:
    """validate client credentials."""
    if client_id == "Insert your client id here" or client_id is None or client_secret == 'Insert your client secret here' or client_secret is None:
        print('*** Please set your client_id and client_secret in the .env file or set Use_SSO to true before running the application. ***')
        raise Exception("Invalid client credentials")
    else:
        print("Using client credentials")


def get_correlation_id() -> str:
	"""Generate a unique correlation ID"""
	return str(uuid.uuid4())


def update_certifi() -> None:
    """Update certifi bundle with certificates from ..."""
    url = CREDENTIALS_ZIP_FILE_URL
    logger.info(f"Downloading credentials zip file from {url}")
    response = requests.get(url)
    response.raise_for_status()
    logger.info("Downloaded certificate zip, size: ", f"{len(response.content)} bytes")

    # Define the names of the certs within the zip file
    root_cert_name = ROOT_CERT_NAME
    issuing_cert_name = ISSUING_CERT_NAME

    try:
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            # Extract the root and issuing certificates
            root_cert_content = z.read(root_cert_name).decode("utf-8")
            issuing_cert_content = z.read(issuing_cert_name).decode("utf-8")
            logger.info("Extracted root and issuing certificates from zip file")

            # Create certrificate bundle in current directory
            import os
            current_dir = os.getcwd()
            custom_cert_path = os.path.join(current_dir, CERT_BUNDLE_NAME)

            # Copy original certifi bundle to current directory
            import shutil
            original_cert_path = certifi.where()
            shutil.copy2(original_cert_path, custom_cert_path)

            with open(custom_cert_path, "a") as cert_bundle:
                cert_bundle.write("\n")
                cert_bundle.write(root_cert_content)
                cert_bundle.write("\n")
                cert_bundle.write(issuing_cert_content)
                cert_bundle.write("\n")

            logger.info(f"Created custom certificate bundle at {custom_cert_path}")
            logger.info("Setting certifi where() to use custom certificate bundle")

            # Overrid certifi.where() to use our custom bundle
            set_custom_cert_path(custom_cert_path)

            # Also set environment variables as backup
            os.enviro["REQUESTS_CA_BUNDLE"] = custom_cert_path
            os.environ["CURL_CA_BUNDLE"] = custom_cert_path
    except KeyError as e:
        # Handle case where expected certs are not found in the zip file
        logger.error(f"Certificate {e} not found in the zip file")
    except Exception as e:
        logger.error(f"An error occurred while updating certifi: {e}")


def get_default_headers_based_on_authentication(use_sso,server_side_token_refresh) -> dict:
    """Get default headers based on authentication method."""
    default_headers = {
        'x-correlation-id': get_correlation_id(),
        'accept' : '*/*',
        'Content-Type': 'application/json'
    }
    if use_sso in ['true', 'True', 'TRUE']:
        auth = authentication_provider.AuthenticationProvider()
        default_headers['Authorization'] = f"Bearer {auth.get_access_token(server_side_token_refresh)}"
    else:
        if server_side_token_refresh:
            auth = authentication_provider.AuthenticationProvider()
            default_headers['Authorization'] = "Basic " + auth.get_basic_credentials()

    return default_headers
    
    
    
    
    
def get_http_client_based_on_authentication(use_sso, server_side_token_refresh):
    """Get HTTP client based on authentication method."""
    if use_sso in ['true', 'True', 'TRUE']:
        http_client = httpx.Client(verify=certifi.where())

    else:
        if server_side_token_refresh:
            http_client = httpx.Client(verify=certifi.where())
        else:
            auth = authentication_provider.AuthenticationProviderWithClientSideTokenRefresh()

            http_client = httpx.Client(auth=auth, verify=certifi.where())


    
