# Stage 1 — Profile Management API

## Overview

A REST API that accepts a name, classifies it using three external APIs
(Genderize, Agify, Nationalize), and stores the result in a PostgreSQL
database.

## Tech Stack

- Python
- FastAPI
- SQLAlchemy
- PostgreSQL (Supabase)
- HTTPX

## Getting Started

### Prerequisites

- Python 3.8+
- PostgreSQL database (Supabase)

### Installation

1. Clone the repository

```bash
   git clone https://github.com/Mastermind-code/Stage_1_Data_Persistence_-_API_Design.git
   cd Stage_1_Data_Persistence_-_API_Design
```

2. Create and activate virtual environment

```bash
   python -m venv venv
   source venv/bin/activate
```

3. Install dependencies

```bash
   pip install -r requirements.txt
```

4. Set up environment variables — create a `.env` file:

5. Run the server

```bash
   uvicorn main:app --reload
```

## API Documentation

### Create Profile

`POST /api/profiles`

**Request Body:**

```json
{ "name": "david" }
```

**Success Response (201):**

```json
{
  "status": "success",
  "data": {
    "id": "019d97a7-6c99-7037-ba9b-699cf40ccdf6",
    "name": "david",
    "gender": "male",
    "gender_probability": 0.99,
    "sample_size": 97517,
    "age": 53,
    "age_group": "adult",
    "country_id": "CM",
    "country_probability": 0.09,
    "created_at": "2026-04-16T18:57:05Z"
  }
}
```

**Duplicate Name Response (200):**

```json
{
  "status": "success",
  "message": "Profile already exists",
  "data": { ... }
}
```

---

### Get All Profiles

`GET /api/profiles`

**Optional filters:**

- `gender` — e.g. `?gender=male`
- `country_id` — e.g. `?country_id=NG`
- `age_group` — e.g. `?age_group=adult`

**Success Response (200):**

```json
{
  "status": "success",
  "count": 1,
  "data": [ ... ]
}
```

---

### Get Single Profile

`GET /api/profiles/{id}`

**Success Response (200):**

```json
{
  "status": "success",
  "data": { ... }
}
```

**Error Response (404):**

```json
{
  "status": "error",
  "message": "Profile not found"
}
```

---

### Delete Profile

`DELETE /api/profiles/{id}`

**Success Response:** `204 No Content`

**Error Response (404):**

```json
{
  "status": "error",
  "message": "Profile not found"
}
```

---

## Error Responses

All errors follow this structure:

```json
{
  "status": "error",
  "message": "<error message>"
}
```

## Author

Adebowale Adam Adewale
