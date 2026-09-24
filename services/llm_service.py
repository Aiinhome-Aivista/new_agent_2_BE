import requests
import json
import re
import time
import os
import sys
from core.config import settings

try:
    from google import genai
    _gemini_available = True
except ImportError:
    _gemini_available = False

try:
    from langchain_google_genai import ChatGoogleGenerativeAI
    _langchain_genai_available = True
except ImportError:
    _langchain_genai_available = False


def _terminal_log(msg: str):
    """Prints directly to terminal with immediate flush and ASCII-safe characters."""
    try:
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()
    except Exception:
        print(msg, flush=True)


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

        model = settings.GEMINI_MODEL or "gemini-flash-lite-latest"
        max_retries = 2
        last_error = None

        for attempt in range(max_retries):
            try:
                _terminal_log("=" * 75)
                _terminal_log(f"[LLM ACTIVE: GOOGLE GEMINI]")
                _terminal_log(f"  Model:     {model}")
                _terminal_log(f"  Status:    Sending request to Google Gemini API ({len(prompt)} chars)...")
                _terminal_log("=" * 75)
                t0 = time.time()
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config={
                        "automatic_function_calling": {"disable": True},
                        "temperature": 0.0,
                        "top_k": 1,
                        "top_p": 1.0,
                    }
                )
                elapsed = time.time() - t0
                _terminal_log("-" * 75)
                _terminal_log(f"[LLM SUCCESS: GOOGLE GEMINI]")
                _terminal_log(f"  Model:     {model}")
                _terminal_log(f"  Duration:  {elapsed:.2f}s")
                _terminal_log("-" * 75)
                return response.text or ""
            except Exception as e:
                last_error = e
                err_str = str(e)
                is_transient = any(k in err_str for k in ["503", "UNAVAILABLE", "high demand", "500", "INTERNAL"])
                if is_transient and attempt < max_retries - 1:
                    wait_time = 2 * (attempt + 1)
                    _terminal_log(f"[LLM RETRY] Gemini ({model}) transient spike ({err_str[:50]}). Retrying in {wait_time}s ({attempt + 1}/{max_retries})...")
                    time.sleep(wait_time)
                else:
                    break

        if last_error:
            raise last_error
        raise RuntimeError(f"Gemini model '{model}' failed.")

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

        _terminal_log("=" * 75)
        _terminal_log(f"[LLM ACTIVE: LOCAL LLM]")
        _terminal_log(f"  Model:     {settings.LLM_MODEL}")
        _terminal_log(f"  Endpoint:  {settings.LLM_API_URL}")
        _terminal_log(f"  Status:    Local LLM inference in progress ({len(prompt)} chars)...")
        _terminal_log("=" * 75)
        t0 = time.time()
        payload = {
            "model": settings.LLM_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.0,
                "top_k": 1,
                "top_p": 1.0,
            }
        }
        response = requests.post(
            settings.LLM_API_URL, 
            json=payload, 
            timeout=(10, settings.LLM_TIMEOUT)
        )
        response.raise_for_status()
        data = response.json()
        elapsed = time.time() - t0
        _terminal_log("-" * 75)
        _terminal_log(f"[LLM SUCCESS: LOCAL LLM]")
        _terminal_log(f"  Model:     {settings.LLM_MODEL}")
        _terminal_log(f"  Duration:  {elapsed:.2f}s")
        _terminal_log("-" * 75)
        return data.get("response", "")

    @classmethod
    def generate(cls, prompt: str) -> str:
        provider = str(getattr(settings, "LLM_PROVIDER", "gemini")).strip().lower()

        # ── STRICT PRIORITY: Always execute configured Gemini model first if API key is present ──
        if settings.GEMINI_API_KEY or provider == "gemini":
            try:
                return cls._call_gemini(prompt)
            except Exception as e:
                configured_model = settings.GEMINI_MODEL or "gemini-flash-lite-latest"
                _terminal_log("*" * 75)
                _terminal_log("[LLM SHIFT: SWITCHING TO LOCAL LLM]")
                _terminal_log(f"  Reason:    Configured Gemini model '{configured_model}' failed")
                _terminal_log(f"  Details:   {str(e)[:100]}")
                _terminal_log(f"  Fallback:  Local LLM ({settings.LLM_MODEL})")
                _terminal_log(f"  Endpoint:  {settings.LLM_API_URL}")
                _terminal_log("*" * 75)
                if settings.LLM_API_URL:
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

    # Convenience alias
    call = generate

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

    @classmethod
    def generate_structured(cls, prompt: str, schema_class, fallback_key: str = None):
        """
        IMPROVEMENT 3: LangChain Structured Output.

        Calls the LLM and enforces output structure at the token level using a
        Pydantic BaseModel schema. Eliminates all json.loads() failures for
        structured JSON extraction calls.

        Args:
            prompt: The prompt string to send to the LLM.
            schema_class: A pydantic BaseModel subclass from agents/llm_schemas.py.
            fallback_key: If the schema wraps items in a dict key (e.g. 'items'),
                          pass that key to extract the list when falling back to
                          generate_json().

        Returns:
            An instance of schema_class on success, or falls back to generate_json()
            result if structured output is unavailable.

        IMPORTANT: Only for structured JSON extraction calls.
        Do NOT use for narrative/prose LLM calls (alerts, summaries, etc.).
        temperature=0 is enforced here for all extraction calls.
        """
        provider = str(getattr(settings, 'LLM_PROVIDER', 'gemini')).strip().lower()

        # Use LangChain structured output only when Gemini + langchain-google-genai available
        if provider == 'gemini' and _langchain_genai_available and settings.GEMINI_API_KEY:
            try:
                llm = ChatGoogleGenerativeAI(
                    model=settings.GEMINI_MODEL or 'gemini-2.0-flash',
                    temperature=0.0,
                    top_k=1,
                    top_p=1.0,
                    google_api_key=settings.GEMINI_API_KEY,
                )
                structured_llm = llm.with_structured_output(schema_class)
                result = structured_llm.invoke(prompt)
                return result
            except Exception as e:
                print(f"[LLMService] Structured output failed ({e}), falling back to generate_json()")

        # Fallback: regular JSON generation + manual parsing
        raw = cls.generate_json(prompt)
        # If raw is a list (some prompts return a list at top level), wrap it
        if isinstance(raw, list) and fallback_key:
            raw = {fallback_key: raw}
        try:
            if isinstance(raw, dict):
                return schema_class(**raw)
            # raw is already valid input
            return schema_class.model_validate(raw)
        except Exception as e:
            print(f"[LLMService] Schema validation on fallback failed ({e}), returning raw dict")
            return raw
