"""Separately versioned HTTP-only CLI transport, using the existing user login.

The pinned CLI ignores overrides of its built-in openai provider. A custom
provider ID retains the built-in OpenAI fields except supports_websockets=false.
No authentication data is opened or copied. Stderr acceptance is unchanged.
"""
from .subscription_client import CliTextClient, configuration

HTTP_PROVIDER_ID = "tau_http"
CLI_VERSION = "0.154.0-alpha.6.2"


def http_configuration(clean):
    return configuration(clean) + [
        'model_provider="tau_http"',
        'model_providers={tau_http={name="OpenAI",wire_api="responses",'
        'requires_openai_auth=true,supports_websockets=false,supports_standalone_web_search=true,'
        'http_headers={version="' + CLI_VERSION + '"},'
        'env_http_headers={"OpenAI-Organization"="OPENAI_ORGANIZATION","OpenAI-Project"="OPENAI_PROJECT"}}}',
    ]


class HttpCliTextClient(CliTextClient):
    def build_configuration(self, clean):
        return http_configuration(clean)
