import json
from sqlalchemy import select, text
from database import SessionLocal
from models.profile import Profile


def seed():
    db = SessionLocal()

    try:
        result = db.execute(text("SELECT current_database(), current_schema()")).fetchone()
        print("Connected to DB:", result[0])
        print("Using schema:", result[1])

        with open("seed_profiles.json", "r") as file:
            payload = json.load(file)
            data = payload.get("profiles", [])

        existing_names = set(db.scalars(select(Profile.name)).all())

        new_profiles = []
        skipped = 0

        for row in data:
            name = row["name"]

            if name in existing_names:
                skipped += 1
                continue

            new_profiles.append(
                Profile(
                    name=row["name"],
                    gender=row["gender"],
                    gender_probability=float(row["gender_probability"]),
                    age=int(row["age"]),
                    age_group=row["age_group"],
                    country_id=row["country_id"],
                    country_name=row["country_name"],
                    country_probability=float(row["country_probability"]),
                )
            )

        if new_profiles:
            db.add_all(new_profiles)
            db.commit()

        print(f"Seeded {len(new_profiles)} profiles")
        print(f"Skipped {skipped} duplicates")

    except Exception as e:
        db.rollback()
        print(f"Seeding failed: {e}")

    finally:
        db.close()


if __name__ == "__main__":
    seed()