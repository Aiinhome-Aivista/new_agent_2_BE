import requests
import json
import re
from core.config import settings
import os

try:
    from google import genai
    if settings.USE_GEMINI and settings.GEMINI_API_KEY:
        gemini_client = genai.Client(api_key=settings.GEMINI_API_KEY)
except ImportError:
    gemini_client = None

class LLMService:
    @classmethod
    def generate(cls, prompt: str) -> str:
        if settings.USE_GEMINI:
            try:
                if not gemini_client:
                    raise RuntimeError("Gemini client is not initialized.")
                
                # Simple retry logic for 429 Rate Limits and 503 Temporary Spikes
                import time
                max_retries = 4
                for attempt in range(max_retries):
                    try:
                        response = gemini_client.models.generate_content(
                            model=settings.GEMINI_MODEL,
                            contents=prompt
                        )
                        return response.text
                    except Exception as e:
                        err_str = str(e)
                        is_client_error = any(code in err_str for code in ["400", "401", "403"])
                        
                        if not is_client_error and attempt < max_retries - 1:
                            wait = 30 if "429" in err_str else 5
                            print(f"Gemini error encountered: {err_str}. Waiting {wait}s before retry (Attempt {attempt + 1}/{max_retries})...")
                            time.sleep(wait)
                        else:
                            print(f"Gemini failed permanently (Quota/Client Error). Falling back to secondary LLM: {settings.LLM_MODEL}")
                            break # Break out of the retry loop to trigger fallback
                            
            except Exception as e:
                print(f"Gemini LLM Error: {e}. Falling back to secondary LLM.")

        payload = {
            "model": settings.LLM_MODEL,
            "prompt": prompt,
            "stream": False
        }
        
        try:
            response = requests.post(
                settings.LLM_API_URL, 
                json=payload, 
                timeout=(10, settings.LLM_TIMEOUT)
            )
            response.raise_for_status()
            data = response.json()
            return data.get("response", "")
        except Exception as e:
            print(f"LLM Error: {e}")
            raise RuntimeError(f"LLM generation failed: {e}")

    @classmethod
    def extract_json(cls, text: str) -> dict:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
            
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
                
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
                print(f"JSON parsing failed, retrying. Error: {e}")
                from core.prompts import get_json_correction_prompt
                correction_prompt = get_json_correction_prompt(raw_response, str(e))
                try:
                    corrected_response = cls.generate(correction_prompt)
                    return cls.extract_json(corrected_response)
                except Exception as retry_e:
                    raise RuntimeError(f"JSON correction retry failed: {retry_e}")
            raise RuntimeError(f"Failed to parse JSON from LLM: {e}")
