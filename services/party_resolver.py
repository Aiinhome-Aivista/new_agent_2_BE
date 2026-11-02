import re
from typing import Dict, List, Optional, Tuple, Any


class PartyResolver:
    """
    Dynamically identifies and resolves Client, Vendor, and Third-Party entities
    from contract preambles, signature blocks, and clause text.
    Strictly eliminates hardcoded client/vendor names across the pipeline.
    """

    _active_parties: Optional[Dict[str, Any]] = None

    @classmethod
    def register_parties(cls, parties: Dict[str, Any]) -> None:
        """Sets the currently active contract party configuration."""
        cls._active_parties = parties

    @classmethod
    def get_active_parties(cls) -> Optional[Dict[str, Any]]:
        return cls._active_parties

    GENERIC_CLIENT_KEYWORDS = [
        "client", "customer", "you", "your", "buyer", "purchaser", "licensee", "principal"
    ]
    GENERIC_VENDOR_KEYWORDS = [
        "vendor", "service provider", "contractor", "consultant", "we", "us", "our", "supplier", "licensor"
    ]

    @classmethod
    def extract_parties_from_preamble(cls, text: str) -> Dict[str, Any]:
        """
        Extracts vendor and client names and their defined aliases/abbreviations
        from contractual preamble text (e.g., 'X ("we" / "Vendor") has been engaged by Y ("you" / "Client")').
        """
        parties = {
            "vendor_name": None,
            "vendor_aliases": set(cls.GENERIC_VENDOR_KEYWORDS),
            "client_name": None,
            "client_aliases": set(cls.GENERIC_CLIENT_KEYWORDS),
        }
        if not text:
            return parties

        # Pattern 1: Vendor engaged by Client
        # e.g., "confirms that Google LLP, ("we"/ "Google") has been engaged by google Limited ("you" / "GL")"
        m1 = re.search(
            r"(?:confirms\s+that|between|by\s+and\s+between)\s+([^,\(\n]+?)(?:,\s*|\s+)\((?:[\"'\u201c\u201d]?we[\"'\u201c\u201d]?\s*(?:/|or|,)?\s*[\"'\u201c\u201d]?([^)\"\u201d]+)[\"'\u201c\u201d]?)\)\s+has\s+been\s+engaged\s+by\s+([^,\(\n]+?)(?:,\s*|\s+)\((?:[\"'\u201c\u201d]?you[\"'\u201c\u201d]?\s*(?:/|or|,)?\s*[\"'\u201c\u201d]?([^)\"\u201d]+)[\"'\u201c\u201d]?)\)",
            text,
            re.IGNORECASE,
        )
        if m1:
            v_name = m1.group(1).strip()
            v_alias = m1.group(2).strip() if m1.group(2) else None
            c_name = m1.group(3).strip()
            c_alias = m1.group(4).strip() if m1.group(4) else None

            parties["vendor_name"] = v_name
            parties["vendor_aliases"].add(v_name.lower())
            if v_alias:
                parties["vendor_aliases"].add(v_alias.lower().strip("\"' \t\r\n"))

            parties["client_name"] = c_name
            parties["client_aliases"].add(c_name.lower())
            if c_alias:
                parties["client_aliases"].add(c_alias.lower().strip("\"' \t\r\n"))
            cls.register_parties(parties)
            return parties

        # Pattern 2: Generic "A ('Vendor') and B ('Client')"
        m2 = re.search(
            r"between\s+([^,\(\n]+?)\s*\((?:the\s+)?[\"'\u201c\u201d]?(?:Vendor|Consultant|Service Provider)[\"'\u201c\u201d]?\)\s+and\s+([^,\(\n]+?)\s*\((?:the\s+)?[\"'\u201c\u201d]?(?:Client|Customer|Company)[\"'\u201c\u201d]?\)",
            text,
            re.IGNORECASE,
        )
        if m2:
            v_name = m2.group(1).strip()
            c_name = m2.group(2).strip()
            parties["vendor_name"] = v_name
            parties["vendor_aliases"].add(v_name.lower())
            parties["client_name"] = c_name
            parties["client_aliases"].add(c_name.lower())
            return parties

        # Fallback: look for "engaged by X"
        m_client = re.search(r"engaged\s+by\s+([^,\(\n]+?)(?:\s*\(|$|\.)", text, re.IGNORECASE)
        if m_client:
            c_name = m_client.group(1).strip()
            parties["client_name"] = c_name
            parties["client_aliases"].add(c_name.lower())

        m_vendor = re.search(r"(?:letter|agreement)\s+confirms\s+that\s+([^,\(\n]+?)(?:\s*\(|,|\.)", text, re.IGNORECASE)
        if m_vendor:
            v_name = m_vendor.group(1).strip()
            parties["vendor_name"] = v_name
            parties["vendor_aliases"].add(v_name.lower())

        return parties

    @classmethod
    def resolve_party_role(cls, text: str, parties: Optional[Dict[str, Any]] = None) -> str:
        """
        Determines whether the text or actor refers to 'Client', 'Vendor', or 'Joint / Unspecified'.
        Returns canonical role string: 'Client', 'Vendor', or 'Joint / Unspecified'.
        """
        if text is None:
            return "Joint / Unspecified"

        text_lower = str(text).lower()

        if parties is None:
            parties = cls.get_active_parties()

        client_aliases = set(cls.GENERIC_CLIENT_KEYWORDS)
        vendor_aliases = set(cls.GENERIC_VENDOR_KEYWORDS)

        if parties:
            client_aliases.update(parties.get("client_aliases", []))
            vendor_aliases.update(parties.get("vendor_aliases", []))
            if parties.get("client_name"):
                client_aliases.add(parties["client_name"].lower())
            if parties.get("vendor_name"):
                vendor_aliases.add(parties["vendor_name"].lower())

        # Exact boundary matches
        def _matches_any(aliases: set, target_text: str) -> bool:
            for alias in aliases:
                if not alias:
                    continue
                pattern = r"\b" + re.escape(alias) + r"\b"
                if re.search(pattern, target_text):
                    return True
            return False

        has_client = _matches_any(client_aliases, text_lower)
        has_vendor = _matches_any(vendor_aliases, text_lower)

        # Disambiguation if client name contains vendor name (e.g. 'Google Limited' contains 'Google')
        if parties and parties.get("client_name") and parties.get("vendor_name"):
            c_lower = parties["client_name"].lower()
            v_lower = parties["vendor_name"].lower()
            if v_lower in c_lower and c_lower in text_lower:
                has_client = True
                # Remove false positive vendor match if vendor name only matched inside client name
                text_without_client = text_lower.replace(c_lower, " ")
                has_vendor = _matches_any(vendor_aliases, text_without_client)

        # Fallback heuristic: corporate suffixes (Limited/Ltd for Client vs LLP/LLC for Vendor)
        if not has_client and not has_vendor:
            if re.search(r'\b[A-Za-z0-9]+(?:\s+[A-Za-z0-9]+)?\s+(?:Limited|Ltd)\b', text, re.IGNORECASE):
                has_client = True
            elif re.search(r'\b[A-Za-z0-9]+(?:\s+[A-Za-z0-9]+)?\s+(?:LLP|LLC)\b', text, re.IGNORECASE):
                has_vendor = True

        if has_client and not has_vendor:
            return "Client"
        if has_vendor and not has_client:
            return "Vendor"
        if has_client and has_vendor:
            return "Joint / Unspecified"

        return "Joint / Unspecified"

    @classmethod
    def get_canonical_actor_name(cls, text: str, parties: Optional[Dict[str, Any]] = None) -> str:
        """
        Returns the resolved specific actor name (e.g. parties['client_name'] or 'Client',
        parties['vendor_name'] or 'Vendor', or 'Joint / Unspecified').
        """
        role = cls.resolve_party_role(text, parties)
        if role == "Client":
            if parties and parties.get("client_name"):
                return parties["client_name"]
            m_lim = re.search(r'\b([A-Za-z0-9]+(?:\s+[A-Za-z0-9]+)?\s+(?:Limited|Ltd))\b', text, re.IGNORECASE)
            if m_lim:
                return m_lim.group(1).strip()
            return "Client"
        if role == "Vendor":
            if parties and parties.get("vendor_name"):
                return parties["vendor_name"]
            m_vend = re.search(r'\b([A-Za-z0-9]+(?:\s+[A-Za-z0-9]+)?\s+(?:LLP|LLC))\b', text, re.IGNORECASE)
            if m_vend:
                return m_vend.group(1).strip()
            return "Vendor"
        return "Joint / Unspecified"
