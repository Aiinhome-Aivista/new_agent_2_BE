"""
Generic Party Resolver and Registry Module
Dynamically identifies and resolves Client, Vendor, and Third-Party entities
from contract preambles, signature blocks, resource planning clauses, and tables.
Enforces strict stem-collision protection and eliminates hardcoded names across the pipeline.
"""

import re
from typing import Dict, List, Optional, Set, Tuple, Any

from core.baseline_config import (
    PRONOUN_MAP,
    GENERIC_CLIENT_KEYWORDS,
    GENERIC_VENDOR_KEYWORDS,
    CORPORATE_SUFFIXES,
    WORK_ACTIVITY_VERBS,
    DEPENDENCY_TAXONOMY,
)


class PartyRegistry:
    """
    Canonical registry for contract parties, defined aliases, pronouns, and supplied roles.
    """

    def __init__(
        self,
        vendor_name: Optional[str] = None,
        client_name: Optional[str] = None,
        vendor_aliases: Optional[Set[str]] = None,
        client_aliases: Optional[Set[str]] = None,
        vendor_roles: Optional[Set[str]] = None,
        client_roles: Optional[Set[str]] = None,
    ):
        self.vendor_name = vendor_name
        self.client_name = client_name
        self.vendor_aliases = set(vendor_aliases or [])
        self.client_aliases = set(client_aliases or [])
        self.vendor_roles = set(vendor_roles or [])
        self.client_roles = set(client_roles or [])

        # Add generic keywords
        self.vendor_aliases.update(k.lower() for k in GENERIC_VENDOR_KEYWORDS)
        self.client_aliases.update(k.lower() for k in GENERIC_CLIENT_KEYWORDS)

        # Detect stem collision
        self.vendor_stem = self._clean_stem(self.vendor_name) if self.vendor_name else ""
        self.client_stem = self._clean_stem(self.client_name) if self.client_name else ""
        self.has_stem_collision = bool(
            self.vendor_stem
            and self.client_stem
            and (self.vendor_stem == self.client_stem or self.vendor_stem in self.client_stem or self.client_stem in self.vendor_stem)
        )

    @staticmethod
    def _clean_stem(name: str) -> str:
        if not name:
            return ""
        tokens = re.findall(r"\b[A-Za-z0-9]+\b", name.lower())
        meaningful = [t for t in tokens if t not in CORPORATE_SUFFIXES]
        return " ".join(meaningful).strip()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vendor_name": self.vendor_name,
            "client_name": self.client_name,
            "vendor_aliases": sorted(list(self.vendor_aliases)),
            "client_aliases": sorted(list(self.client_aliases)),
            "vendor_roles": sorted(list(self.vendor_roles)),
            "client_roles": sorted(list(self.client_roles)),
            "has_stem_collision": self.has_stem_collision,
        }

    @classmethod
    def from_contract_text(cls, text: str) -> "PartyRegistry":
        return PartyResolver.extract_parties_from_preamble(text)

    def resolve_party(self, text: str) -> str:
        return PartyResolver.resolve_party_role(text, registry=self)


class PartyResolver:
    """
    Generic party identification, role resolution, and sentence ownership engine.
    """

    _active_registry: Optional[PartyRegistry] = None

    @classmethod
    def register_parties(cls, registry_or_dict: Any) -> None:
        if isinstance(registry_or_dict, PartyRegistry):
            cls._active_registry = registry_or_dict
        elif isinstance(registry_or_dict, dict):
            cls._active_registry = PartyRegistry(
                vendor_name=registry_or_dict.get("vendor_name"),
                client_name=registry_or_dict.get("client_name"),
                vendor_aliases=set(registry_or_dict.get("vendor_aliases", [])),
                client_aliases=set(registry_or_dict.get("client_aliases", [])),
                vendor_roles=set(registry_or_dict.get("vendor_roles", [])),
                client_roles=set(registry_or_dict.get("client_roles", [])),
            )

    @classmethod
    def get_active_parties(cls) -> Optional[Dict[str, Any]]:
        if cls._active_registry:
            return cls._active_registry.to_dict()
        return None

    @classmethod
    def get_active_registry(cls) -> Optional[PartyRegistry]:
        return cls._active_registry

    @classmethod
    def extract_parties_from_preamble(cls, text: str) -> PartyRegistry:
        """
        Builds a party registry from preamble text and signature blocks.
        Detects exact legal names and defined abbreviations/aliases (e.g. ("we" / "Vendor")).
        """
        vendor_name = None
        client_name = None
        vendor_aliases: Set[str] = set()
        client_aliases: Set[str] = set()
        vendor_roles: Set[str] = set()
        client_roles: Set[str] = set()

        if not text:
            reg = PartyRegistry()
            cls.register_parties(reg)
            return reg

        # Pattern 1: Vendor engaged by Client with parenthetical aliases
        # e.g., "confirms that [Party 1] ("we" / "[Alias 1]") has been engaged by [Party 2] ("you" / "[Alias 2]")"
        p1 = re.compile(
            r"(?:confirms\s+that|between|by\s+and\s+between)\s+([^,\(\n]+?)(?:,\s*|\s+)\((?:[\"'\u201c\u201d]?we[\"'\u201c\u201d]?\s*(?:/|or|,)?\s*[\"'\u201c\u201d]?([^)\"\u201d]+)[\"'\u201c\u201d]?)\)\s+has\s+been\s+engaged\s+by\s+([^,\(\n]+?)(?:,\s*|\s+)\((?:[\"'\u201c\u201d]?you[\"'\u201c\u201d]?\s*(?:/|or|,)?\s*[\"'\u201c\u201d]?([^)\"\u201d]+)[\"'\u201c\u201d]?)\)",
            re.IGNORECASE,
        )
        m1 = p1.search(text)
        if m1:
            vendor_name = m1.group(1).strip()
            v_al = m1.group(2).strip().strip("\"' \t\r\n") if m1.group(2) else ""
            client_name = m1.group(3).strip()
            c_al = m1.group(4).strip().strip("\"' \t\r\n") if m1.group(4) else ""

            if vendor_name:
                vendor_aliases.add(vendor_name.lower())
            if v_al:
                vendor_aliases.add(v_al.lower())
            if client_name:
                client_aliases.add(client_name.lower())
            if c_al:
                client_aliases.add(c_al.lower())

        # Pattern 2: Generic "Party A ('Vendor' or 'ALIAS') and Party B ('Client' or 'ALIAS')"
        if not vendor_name or not client_name:
            p2 = re.compile(
                r"between\s+([^,\(\n]+?)\s*\((?:the\s+)?[\"'\u201c\u201d]?(?:Vendor|Consultant|Service Provider)[\"'\u201c\u201d]?(?:\s*(?:or|/|,)\s*[\"'\u201c\u201d]?([^)\"\u201d]+)[\"'\u201c\u201d]?)?\)\s+and\s+([^,\(\n]+?)\s*\((?:the\s+)?[\"'\u201c\u201d]?(?:Client|Customer|Company)[\"'\u201c\u201d]?(?:\s*(?:or|/|,)\s*[\"'\u201c\u201d]?([^)\"\u201d]+)[\"'\u201c\u201d]?)?\)",
                re.IGNORECASE,
            )
            m2 = p2.search(text)
            if m2:
                vendor_name = vendor_name or m2.group(1).strip()
                v2_al = m2.group(2).strip().strip("\"' \t\r\n") if m2.group(2) else ""
                client_name = client_name or m2.group(3).strip()
                c2_al = m2.group(4).strip().strip("\"' \t\r\n") if m2.group(4) else ""
                if vendor_name:
                    vendor_aliases.add(vendor_name.lower())
                if v2_al:
                    vendor_aliases.add(v2_al.lower())
                if client_name:
                    client_aliases.add(client_name.lower())
                if c2_al:
                    client_aliases.add(c2_al.lower())

        # Pattern 3: Fallback 'engaged by [Client]'
        if not client_name:
            mc = re.search(r"engaged\s+by\s+([^,\(\n]+?)(?:\s*\(|$|\.)", text, re.IGNORECASE)
            if mc:
                client_name = mc.group(1).strip()
                client_aliases.add(client_name.lower())

        if not vendor_name:
            mv = re.search(r"(?:letter|agreement)\s+confirms\s+that\s+([^,\(\n]+?)(?:\s*\(|,|\.)", text, re.IGNORECASE)
            if mv:
                vendor_name = mv.group(1).strip()
                vendor_aliases.add(vendor_name.lower())

        # Scan resource planning for vendor development team roles
        # e.g., "Resource Planning for Development Team: Project Manager - 1, Technical Lead - 1, Application developer - 5"
        res_plan_match = re.search(
            r"(?:resource\s+planning|resourcing\s+plan|development\s+team\s+resources)[^\n]*\n([\s\S]*?)(?=\n\s*(?:[A-Z0-9#\*\.]+\s+|project\s+overall|\Z))",
            text,
            re.IGNORECASE,
        )
        if res_plan_match:
            plan_text = res_plan_match.group(1)
            for line in plan_text.split("\n"):
                line_clean = re.sub(r"^\s*[-*\d\.\)\s]+", "", line).strip()
                if line_clean:
                    role_candidate = re.split(r"[-–—:]|\b\d+\b", line_clean)[0].strip()
                    if role_candidate and len(role_candidate) > 2:
                        vendor_roles.add(role_candidate.lower())

        registry = PartyRegistry(
            vendor_name=vendor_name,
            client_name=client_name,
            vendor_aliases=vendor_aliases,
            client_aliases=client_aliases,
            vendor_roles=vendor_roles,
            client_roles=client_roles,
        )
        cls.register_parties(registry)
        return registry

    @classmethod
    def resolve_party_role(cls, text: str, registry: Optional[PartyRegistry] = None) -> str:
        """
        Resolves role for an actor string or sentence into 'Client', 'Vendor', or 'Joint / Unspecified'.
        Strictly prevents stem-collision false matches.
        """
        if not text:
            return "Joint / Unspecified"

        text_str = str(text).strip()
        text_lower = text_str.lower()

        reg = registry or cls._active_registry
        if not reg:
            reg = PartyRegistry()

        # 1. Check supplied roles first if text matches a known role title
        cleaned_text = re.sub(r"^\s*[-*\d\.\)\s]+", "", text_lower).strip()
        for v_role in reg.vendor_roles:
            if v_role and (v_role in cleaned_text or cleaned_text in v_role):
                return "Vendor"
        for c_role in reg.client_roles:
            if c_role and (c_role in cleaned_text or cleaned_text in c_role):
                return "Client"

        # 2. Check exact aliases
        # If stem collision is active (e.g. Google Limited vs Google LLP), aliases like 'GL' or exact legal names must match
        def _has_boundary_match(alias: str, target: str) -> bool:
            if not alias or len(alias) < 2:
                return False
            pattern = r"(?<!\w)" + re.escape(alias) + r"(?!\w)"
            return bool(re.search(pattern, target))

        # Check full legal names first (with corporate suffixes)
        has_client_legal = bool(reg.client_name and _has_boundary_match(reg.client_name.lower(), text_lower))
        has_vendor_legal = bool(reg.vendor_name and _has_boundary_match(reg.vendor_name.lower(), text_lower))

        if has_client_legal and not has_vendor_legal:
            return "Client"
        if has_vendor_legal and not has_client_legal:
            return "Vendor"

        # Check exact explicit aliases defined in contract
        client_custom_aliases = {a for a in reg.client_aliases if a not in GENERIC_CLIENT_KEYWORDS and a != (reg.client_name or "").lower()}
        vendor_custom_aliases = {a for a in reg.vendor_aliases if a not in GENERIC_VENDOR_KEYWORDS and a != (reg.vendor_name or "").lower()}

        has_client_custom = any(_has_boundary_match(a, text_lower) for a in client_custom_aliases)
        has_vendor_custom = any(_has_boundary_match(a, text_lower) for a in vendor_custom_aliases)

        if has_client_custom and not has_vendor_custom:
            return "Client"
        if has_vendor_custom and not has_client_custom:
            return "Vendor"

        # Check pronouns via PRONOUN_MAP
        words = re.findall(r"\b[A-Za-z]+\b", text_lower)
        pronoun_vendors = sum(1 for w in words if PRONOUN_MAP.get(w) == "VENDOR")
        pronoun_clients = sum(1 for w in words if PRONOUN_MAP.get(w) == "CLIENT")

        if pronoun_vendors > 0 and pronoun_clients == 0:
            return "Vendor"
        if pronoun_clients > 0 and pronoun_vendors == 0:
            return "Client"

        # Check generic keywords if no stem collision ambiguity
        has_gen_client = any(_has_boundary_match(k, text_lower) for k in GENERIC_CLIENT_KEYWORDS)
        has_gen_vendor = any(_has_boundary_match(k, text_lower) for k in GENERIC_VENDOR_KEYWORDS)

        # Corporate suffix heuristics when legal name stem collides
        if reg.has_stem_collision:
            # Check corporate suffixes in text
            client_suffix_in_name = any(s in (reg.client_name or "").lower() for s in CORPORATE_SUFFIXES)
            vendor_suffix_in_name = any(s in (reg.vendor_name or "").lower() for s in CORPORATE_SUFFIXES)
            
            # If text has limited/ltd and client name has limited/ltd -> Client
            for s in CORPORATE_SUFFIXES:
                if _has_boundary_match(s, text_lower):
                    if reg.client_name and s in reg.client_name.lower():
                        return "Client"
                    if reg.vendor_name and s in reg.vendor_name.lower():
                        return "Vendor"

        if has_gen_client and not has_gen_vendor:
            return "Client"
        if has_gen_vendor and not has_gen_client:
            return "Vendor"
        if has_gen_client and has_gen_vendor:
            return "Joint / Unspecified"

        return "Joint / Unspecified"

    @classmethod
    def get_canonical_actor_name(cls, text: str, registry: Optional[PartyRegistry] = None) -> str:
        role = cls.resolve_party_role(text, registry)
        reg = registry or cls._active_registry
        if role == "Client":
            return reg.client_name if (reg and reg.client_name) else "Client"
        if role == "Vendor":
            return reg.vendor_name if (reg and reg.vendor_name) else "Vendor"
        return "Joint / Unspecified"

    @classmethod
    def is_vendor_activity_duty(cls, duty_text: str) -> bool:
        """
        Determines whether a duty describes an actionable engineering, delivery,
        or project management activity that should qualify as VENDOR_WORK (category ACTIVITY).
        """
        if not duty_text:
            return False
        tokens = set(re.findall(r"\b[A-Za-z]+\b", duty_text.lower()))
        return bool(tokens.intersection(WORK_ACTIVITY_VERBS))
