import requests
import json
import re
import time
import os
from core.config import settings

try:
    from google import genai
    _gemini_available = True
except ImportError:
    _gemini_available = False


class LLMService:
    @classmethod
    def _get_gemini_client(cls):
        if not _gemini_available:
            return None
        if settings.GEMINI_API_KEY:
            return genai.Client(api_key=settings.GEMINI_API_KEY)
        return None

    @classmethod
    def _call_gemini(cls, prompt: str) -> str:
        client = cls._get_gemini_client()
        if not client:
            raise RuntimeError("Gemini API key is not configured or google-genai library is missing.")

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model=settings.GEMINI_MODEL,
                    contents=prompt
                )
                return response.text or ""
            except Exception as e:
                err_str = str(e)
                is_quota = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str
                if is_quota and attempt < max_retries - 1:
                    wait_time = 15 * (attempt + 1)
                    print(f"[LLMService] Gemini rate limit encountered. Waiting {wait_time}s before retry ({attempt + 1}/{max_retries})...")
                    time.sleep(wait_time)
                else:
                    raise e

    @classmethod
    def _call_openai(cls, prompt: str) -> str:
        if not settings.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not configured in .env")

        base_url = (settings.OPENAI_BASE_URL.rstrip('/') if settings.OPENAI_BASE_URL else "https://api.openai.com/v1")
        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": settings.OPENAI_MODEL or "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.2
        }
        
        response = requests.post(url, headers=headers, json=payload, timeout=(10, settings.LLM_TIMEOUT))
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    @classmethod
    def _call_claude(cls, prompt: str) -> str:
        if not settings.ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured in .env")

        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": settings.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"
        }
        payload = {
            "model": settings.ANTHROPIC_MODEL or "claude-3-5-sonnet-20241022",
            "max_tokens": 4096,
            "messages": [
                {"role": "user", "content": prompt}
            ]
        }
        
        response = requests.post(url, headers=headers, json=payload, timeout=(10, settings.LLM_TIMEOUT))
        response.raise_for_status()
        data = response.json()
        return data["content"][0]["text"]

    @classmethod
    def _call_azure_openai(cls, prompt: str) -> str:
        if not settings.AZURE_OPENAI_API_KEY or not settings.AZURE_OPENAI_ENDPOINT:
            raise RuntimeError("AZURE_OPENAI_API_KEY or AZURE_OPENAI_ENDPOINT is not configured in .env")

        endpoint = settings.AZURE_OPENAI_ENDPOINT.rstrip('/')
        deployment = settings.AZURE_OPENAI_DEPLOYMENT
        api_version = settings.AZURE_OPENAI_API_VERSION or "2024-02-01"
        url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"
        
        headers = {
            "api-key": settings.AZURE_OPENAI_API_KEY,
            "Content-Type": "application/json"
        }
        payload = {
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.2
        }
        
        response = requests.post(url, headers=headers, json=payload, timeout=(10, settings.LLM_TIMEOUT))
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    @classmethod
    def _call_custom(cls, prompt: str) -> str:
        if not settings.LLM_API_URL:
            raise RuntimeError("LLM_API_URL is not configured for custom/local LLM provider.")

        payload = {
            "model": settings.LLM_MODEL,
            "prompt": prompt,
            "stream": False
        }
        response = requests.post(
            settings.LLM_API_URL, 
            json=payload, 
            timeout=(10, settings.LLM_TIMEOUT)
        )
        response.raise_for_status()
        data = response.json()
        return data.get("response", "")

    @classmethod
    def generate(cls, prompt: str) -> str:
        provider = str(getattr(settings, "LLM_PROVIDER", "gemini")).strip().lower()

        if provider == "gemini":
            try:
                return cls._call_gemini(prompt)
            except Exception as e:
                print(f"[LLMService] Gemini error: {e}. Checking fallback...")
                if settings.LLM_API_URL:
                    print(f"[LLMService] Falling back to custom LLM ({settings.LLM_MODEL})...")
                    return cls._call_custom(prompt)
                raise e

        elif provider in ["openai", "chatgpt"]:
            try:
                return cls._call_openai(prompt)
            except Exception as e:
                print(f"[LLMService] OpenAI error: {e}. Checking fallback...")
                if settings.GEMINI_API_KEY:
                    print("[LLMService] Falling back to Gemini...")
                    return cls._call_gemini(prompt)
                raise e

        elif provider in ["claude", "anthropic"]:
            try:
                return cls._call_claude(prompt)
            except Exception as e:
                print(f"[LLMService] Claude error: {e}. Checking fallback...")
                if settings.GEMINI_API_KEY:
                    print("[LLMService] Falling back to Gemini...")
                    return cls._call_gemini(prompt)
                raise e

        elif provider in ["azure_openai", "azure", "copilot"]:
            try:
                return cls._call_azure_openai(prompt)
            except Exception as e:
                print(f"[LLMService] Azure OpenAI error: {e}. Checking fallback...")
                if settings.GEMINI_API_KEY:
                    print("[LLMService] Falling back to Gemini...")
                    return cls._call_gemini(prompt)
                raise e

        elif provider in ["custom", "local", "ollama"]:
            return cls._call_custom(prompt)

        else:
            raise ValueError(f"Unsupported LLM_PROVIDER: '{provider}'. Supported: gemini, openai, claude, azure_openai, custom.")

    @classmethod
    def extract_json(cls, text: str) -> dict:
        """Extracts and parses JSON object or array from raw LLM output."""
        cleaned = text.strip()
        
        # Remove markdown code fences if wrapped (e.g. ```json ... ```)
        if cleaned.startswith("```"):
            cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
            cleaned = re.sub(r'\s*```$', '', cleaned)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Regex search for JSON object
        match_obj = re.search(r'\{.*\}', text, re.DOTALL)
        if match_obj:
            try:
                return json.loads(match_obj.group(0))
            except json.JSONDecodeError:
                pass

        # Regex search for JSON array
        match_arr = re.search(r'\[.*\]', text, re.DOTALL)
        if match_arr:
            try:
                return json.loads(match_arr.group(0))
            except json.JSONDecodeError:
                pass

        raise ValueError("Could not extract valid JSON from the response text.")

    @classmethod
    def generate_json(cls, prompt: str, retry_count: int = 1) -> dict:
        """Generates JSON, with one retry if parsing fails."""
        try:
            raw_response = cls.generate(prompt)
            return cls.extract_json(raw_response)
        except (json.JSONDecodeError, ValueError) as e:
            if retry_count > 0:
                print(f"[LLMService] JSON parsing failed, retrying with correction prompt. Error: {e}")
                from core.prompts import get_json_correction_prompt
                correction_prompt = get_json_correction_prompt(raw_response, str(e))
                try:
                    corrected_response = cls.generate(correction_prompt)
                    return cls.extract_json(corrected_response)
                except Exception as retry_e:
                    raise RuntimeError(f"JSON correction retry failed: {retry_e}")
            raise RuntimeError(f"Failed to parse JSON from LLM: {e}")
