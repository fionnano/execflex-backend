class RoleDetails:
    def __init__(self, role_title, company_name, industry, role_description,
                 experience_level, commitment, location, budget_range, role_type, contact_name, contact_email):
        # Validating each input value before setting them
        if not all([role_title, company_name, industry, role_description, experience_level, 
                    commitment, location, budget_range, role_type, contact_name, contact_email]):
            raise ValueError("All fields are required")

        self.role_title = role_title
        self.company_name = company_name
        self.industry = industry
        self.role_description = role_description
        self.experience_level = experience_level
        self.commitment = commitment
        self.location = location
        self.budget_range = budget_range
        self.role_type = role_type
        self.contact_name = contact_name
        self.contact_email = contact_email

    def to_dict(self):
        # Return all the role details in dictionary format for easier handling/storage
        return {
            "role_title": self.role_title,
            "company_name": self.company_name,
            "industry": self.industry,
            "role_description": self.role_description,
            "experience_level": self.experience_level,
            "commitment": self.commitment,
            "location": self.location,
            "budget_range": self.budget_range,
            "role_type": self.role_type,
            "contact_name": self.contact_name,
            "contact_email": self.contact_email,
        }
