import httpx

AGIFY_URL = "https://api.agify.io"

async def fetch_user_age(name: str):
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(AGIFY_URL, params={"name": name})
            response.raise_for_status()
            
            data = response.json()
            if data.get("age") is None:
                return None, "Agify returned an invalid response for the provided name"
            
            age = data.get("age")

            if age <= 12:
                age_group = "child"
            elif age <= 19:
                age_group = "teenager"
            elif age <= 59:
                age_group = "adult"
            else:
                age_group = "senior"

            return {'age': age, 'age_group': age_group}, None
        except httpx.HTTPStatusError as e:
            return None, f"Upstream API error: {e.response.status_code}"
        except (httpx.ConnectError, httpx.TimeoutException):
            return None, "External service is unreachable or timed out"
        except Exception as e:
            print(f"UNEXPECTED ERROR: {type(e).__name__}: {e}")
            return None, "An unexpected error occurred"