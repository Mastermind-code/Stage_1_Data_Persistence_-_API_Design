import json
from database import SessionLocal
from models.profile import Profile


def seed():
    db = SessionLocal()
    
    try:
        with open("seed_profiles.json", "r") as file:  # what file are you opening?
            data = json.load(file)
            count = 0

            for row in data:
                # 1. Check if profile already exists
                existing = db.query(Profile).filter(
                    Profile.name == row['name']
                ).first()
                
                if existing:
                    continue  # skip duplicates
                
                # 2. Create new profile
                profile = Profile(
                    name=row['name'],
                    gender=row['gender'],
                    gender_probability=float(row['gender_probability']),
                    age=int(row['age']),
                    age_group=row['age_group'],
                    country_id=row['country_id'],
                    country_name=row['country_name'],
                    country_probability=float(row['country_probability']),
                )
                
                db.add(profile)
                count += 1
            
            db.commit()
            print(f"Seeded {count} profiles")
    
    except Exception as e:
        db.rollback()
        print(f"Seeding failed: {e}")
    
    finally:
        db.close()

if __name__ == "__main__":
    seed()