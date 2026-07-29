import re

class StakeholderExtractor:
    """
    Extracts stakeholder/contact information from document chunks.
    Detects name + role patterns, email addresses, and responsibility assignments.
    """
    
    ROLE_KEYWORDS = [
        "engagement partner", "engagement manager", "project manager",
        "project lead", "team lead", "account manager", "delivery manager",
        "technical lead", "architect", "consultant", "director",
        "partner", "senior manager", "manager", "lead", "head",
        "coordinator", "analyst", "sponsor", "owner",
        "cto", "cio", "cfo", "ceo", "vp", "avp"
    ]
    
    # Patterns: "Name – Role" or "Name, Role" or "Role: Name"
    NAME_ROLE_PATTERNS = [
        # "John Smith – Engagement Partner" or "John Smith - Project Manager"
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s*[\–\-\—]\s*(.+)',
        # "John Smith, Engagement Partner"
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s*,\s*(.+)',
        # "Engagement Partner: John Smith"
        r'(.+?):\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s*$',
    ]
    
    EMAIL_PATTERN = r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'

    @classmethod
    def extract(cls, chunks: list[dict]) -> list[dict]:
        """
        Extracts stakeholders from document chunks.
        Returns: [{name, role, email, responsibility}]
        """
        stakeholders = []
        seen_names = set()
        
        for chunk in chunks:
            text = chunk.get("text", "")
            lines = text.split("\n")
            
            for line in lines:
                line = line.strip()
                if not line or len(line) < 5:
                    continue
                
                # Strategy 1: Name – Role patterns
                for pattern in cls.NAME_ROLE_PATTERNS:
                    match = re.search(pattern, line)
                    if match:
                        part1 = match.group(1).strip()
                        part2 = match.group(2).strip()
                        
                        # Determine which part is name and which is role
                        name, role = None, None
                        
                        # Check if part2 contains a role keyword → part1 is name
                        if any(rk in part2.lower() for rk in cls.ROLE_KEYWORDS):
                            name = part1
                            role = part2.rstrip('.;,')
                        # Check if part1 contains a role keyword → part2 is name
                        elif any(rk in part1.lower() for rk in cls.ROLE_KEYWORDS):
                            name = part2
                            role = part1.rstrip('.;,:')
                        
                        if name and role and len(name) > 3 and len(name) < 50:
                            name_key = name.lower().strip()
                            if name_key not in seen_names:
                                seen_names.add(name_key)
                                
                                # Look for email in the same line or nearby
                                email = None
                                email_match = re.search(cls.EMAIL_PATTERN, line)
                                if email_match:
                                    email = email_match.group(0)
                                
                                stakeholders.append({
                                    "name": name,
                                    "role": role,
                                    "email": email,
                                    "responsibility": None
                                })
                        break
                
                # Strategy 2: Table rows with name | role | email
                if "|" in line:
                    parts = [p.strip() for p in line.split("|") if p.strip()]
                    if len(parts) >= 2:
                        potential_name = parts[0]
                        potential_role = parts[1] if len(parts) > 1 else None
                        
                        # Validate it looks like a person's name
                        if (re.match(r'^[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}$', potential_name) and
                            potential_role and 
                            any(rk in potential_role.lower() for rk in cls.ROLE_KEYWORDS)):
                            
                            name_key = potential_name.lower().strip()
                            if name_key not in seen_names:
                                seen_names.add(name_key)
                                
                                email = None
                                for part in parts:
                                    email_match = re.search(cls.EMAIL_PATTERN, part)
                                    if email_match:
                                        email = email_match.group(0)
                                        break
                                
                                responsibility = parts[2] if len(parts) > 2 and not re.search(cls.EMAIL_PATTERN, parts[2]) else None
                                
                                stakeholders.append({
                                    "name": potential_name,
                                    "role": potential_role.rstrip('.;,'),
                                    "email": email,
                                    "responsibility": responsibility
                                })
                
                # Strategy 3: Standalone email with name context
                email_match = re.search(cls.EMAIL_PATTERN, line)
                if email_match:
                    email = email_match.group(0)
                    # Try to extract name from the email prefix or surrounding text
                    email_prefix = email.split('@')[0]
                    # Check if there's a name before the email in the line
                    before_email = line[:line.index(email)].strip().rstrip(':,-–')
                    name_match = re.search(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})', before_email)
                    if name_match:
                        name = name_match.group(1)
                        name_key = name.lower().strip()
                        if name_key not in seen_names:
                            seen_names.add(name_key)
                            stakeholders.append({
                                "name": name,
                                "role": "Stakeholder",
                                "email": email,
                                "responsibility": None
                            })
        
        return stakeholders
