"""Ollama client wrapper."""

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class OllamaClient:
    """Client for the Ollama generate API."""

    def __init__(self, base_url: str, model: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.api_endpoint = f"{self.base_url}/api/generate"

    def generate(self, prompt: str, temperature: float = 0.3, max_tokens: int = 500) -> Optional[str]:
        """Generate text with the configured Ollama model."""
        try:
            payload = {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                    "top_p": 0.9,
                    "top_k": 50,
                    "repeat_penalty": 1.05,
                },
            }

            response = requests.post(self.api_endpoint, json=payload, timeout=self.timeout)

            if response.status_code == 200:
                generated_text = response.json().get("response", "").strip()
                logger.info("Ollama generated response (%s chars)", len(generated_text))
                return generated_text

            logger.error("Ollama error: %s", response.status_code)
            logger.error("Response: %s", response.text)
            return None

        except requests.exceptions.ConnectionError:
            logger.error("Cannot connect to Ollama at %s", self.base_url)
            logger.error("Ensure Ollama is running: ollama serve")
            return None
        except requests.exceptions.Timeout:
            logger.error("Ollama request timeout (%ss)", self.timeout)
            return None
        except Exception as exc:
            logger.error("Ollama error: %s: %s", type(exc).__name__, exc)
            return None

    def check_health(self) -> bool:
        """Check if Ollama is running and the configured model is pulled."""
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if response.status_code != 200:
                logger.warning("Ollama health check failed: %s", response.status_code)
                return False

            names = {
                model.get("name", "").lower()
                for model in response.json().get("models", [])
            }
            configured = self.model.lower()
            configured_latest = configured if ":" in configured else f"{configured}:latest"

            if configured not in names and configured_latest not in names:
                logger.warning("Ollama is running, but model is not pulled: %s", self.model)
                logger.warning("Run: ollama pull %s", self.model)
                return False

            logger.info("Ollama model is available: %s", self.model)
            return True

        except Exception as exc:
            logger.warning("Ollama health check failed: %s", exc)
            return False
