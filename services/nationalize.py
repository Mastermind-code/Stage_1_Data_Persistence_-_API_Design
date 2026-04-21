import httpx
import pycountry
NATIONALIZE_URL = "https://api.nationalize.io"

async def fetch_user_nationality(name: str):
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(NATIONALIZE_URL, params={"name": name})
            response.raise_for_status()
            
            data = response.json()
            if not data.get("country"):
                return None, "Nationalize returned an invalid response for the provided name"
            
            countries = data.get("country")
            top_country = max(countries, key=lambda x: x.get("probability", 0))
            country_code = top_country['country_id']
            country = pycountry.countries.get(alpha_2=country_code)
            country_name = country.name if country else country_code
            return {'country_id': top_country['country_id'], 
                    'country_name': country_name,
                    'country_probability': top_country['probability']}, None
        except httpx.HTTPStatusError as e:
            return None, f"Upstream API error: {e.response.status_code}"
        except (httpx.ConnectError, httpx.TimeoutException):
            return None, "External service is unreachable or timed out"
        except Exception as e:  
            print(f"UNEXPECTED ERROR: {type(e).__name__}: {e}")
            return None, "An unexpected error occurred"