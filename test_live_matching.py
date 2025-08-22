from modules.match_finder import find_best_match

# These are sample filter values to test — you can change them
industry = "Technology"
expertise = "Director"
availability = "part_time"
min_experience = 5
max_salary = 150000
location = "London"

matches = find_best_match(
    industry=industry,
    expertise=expertise,
    availability=availability,
    min_experience=min_experience,
    max_salary=max_salary,
    location=location
)

print("\n🔎 Matches Found:")
for m in matches:
    print(f"- {m['title']} at {m['company_info']['name']} in {m['location']}")
