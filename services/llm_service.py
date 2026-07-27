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
        # SIMULATE 429 QUOTA ERROR
        raise Exception("Gemini generation failed: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details.'}}")
        if settings.USE_GEMINI:
            try:
                if not gemini_client:
                    raise RuntimeError("Gemini client is not initialized.")
                
                # Simple retry logic for 429 Rate Limits
                import time
                max_retries = 2
                for attempt in range(max_retries):
                    try:
                        response = gemini_client.models.generate_content(
                            model='gemini-3.5-flash',
                            contents=prompt
                        )
                        return response.text
                    except Exception as e:
                        err_str = str(e)
                        # Only retry on per-minute rate limit, not daily quota exhaustion
                        if "429" in err_str and "GenerateRequestsPerDayPerProjectPerModel" not in err_str and attempt < max_retries - 1:
                            wait = 30
                            print(f"Gemini RPM limit hit. Waiting {wait}s (Attempt {attempt + 1}/{max_retries})...")
                            time.sleep(wait)
                        else:
                            raise e
                            
            except Exception as e:
                print(f"Gemini LLM Error: {e}")
                raise RuntimeError(f"Gemini generation failed: {e}")

        payload = {
            "model": settings.LLM_MODEL,
            "prompt": prompt,
            "stream": False
        }
        
        try:
            response = requests.post(
                settings.LLM_API_URL, 
                json=payload, 
                timeout=settings.LLM_TIMEOUT
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
                correction_prompt = f"The following text was supposed to be valid JSON but failed to parse. Please output ONLY the corrected valid JSON and nothing else.\n\nInvalid Text:\n{raw_response}\n\nError:\n{e}"
                try:
                    corrected_response = cls.generate(correction_prompt)
                    return cls.extract_json(corrected_response)
                except Exception as retry_e:
                    raise RuntimeError(f"JSON correction retry failed: {retry_e}")
            raise RuntimeError(f"Failed to parse JSON from LLM: {e}")
