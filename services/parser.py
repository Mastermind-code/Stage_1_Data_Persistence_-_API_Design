import re
import pycountry

def parse_query(query: str) -> dict:
    query = query.lower().strip()
    filters = {}

    
    if "female" in query:
        filters['gender'] = "female"
    elif "male" in query:
        filters['gender'] = "male"

    
    if "young" in query:
        filters["min_age"] = 16
        filters["max_age"] = 24
    
    for group in ["child", "teenager", "adult", "senior"]:
        if group in query:
            filters["age_group"] = group

   
    above_match = re.search(r'(?:above|over)\s*(\d+)', query)
    if above_match:
        filters['min_age'] = int(above_match.group(1))

    below_match = re.search(r'(?:below|under)\s*(\d+)', query)
    if below_match:
        filters['max_age'] = int(below_match.group(1))


    from_match = re.search(r'from\s+([a-z\s]+?)(?:\s+(?:above|below|over|under|and|who|with)|$)', query)
    if from_match:
        country_query = from_match.group(1).strip()
        try:
            results = pycountry.countries.search_fuzzy(country_query)
            if results:
                filters["country_id"] = results[0].alpha_2
        except LookupError:
            pass

    return filters if filters else None
