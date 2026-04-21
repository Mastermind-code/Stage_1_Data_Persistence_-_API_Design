import httpx

GENDERIZE_URL = "https://api.genderize.io"

async def fetch_user_data(name: str):
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(GENDERIZE_URL, params={"name": name})
            response.raise_for_status()
            
            data = response.json()
            if data.get("gender") is None or data.get("count", 0) == 0:
                return None, "No prediction available for the provided name"
            gender_probability = data.get("probability", 0)
            return {'gender': data.get("gender"), 'gender_probability': gender_probability
                    }, None
        except httpx.HTTPStatusError as e:
            return None, f"Upstream API error: {e.response.status_code}"
        except (httpx.ConnectError, httpx.TimeoutException):
            return None, "External service is unreachable or timed out"
        except Exception as e:
            print(f"UNEXPECTED ERROR: {type(e).__name__}: {e}")
            return None, "An unexpected error occurred"
        