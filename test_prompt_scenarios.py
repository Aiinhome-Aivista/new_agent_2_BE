import unittest
from services.dependency_graph_builder import DependencyGraphBuilder
from services.validation_service import ValidationService

class TestPromptScenarios(unittest.TestCase):

    def test_scenario_2_fake_status(self):
        # Input: "Security review is pending."
        known_entities = {"Security Review"}
        
        self.assertTrue(DependencyGraphBuilder.is_valid_dependency_entity("Security Review", known_entities))
        self.assertTrue(DependencyGraphBuilder.is_valid_dependency_entity("CRM Integration", known_entities))
        
        # Test invalid nodes
        self.assertFalse(DependencyGraphBuilder.is_valid_dependency_entity("Pending", known_entities))
        self.assertFalse(DependencyGraphBuilder.is_valid_dependency_entity("Pending review", known_entities))
        self.assertFalse(DependencyGraphBuilder.is_valid_dependency_entity("In progress", known_entities))
        self.assertFalse(DependencyGraphBuilder.is_valid_dependency_entity("Unknown", known_entities))

    def test_scenario_3_owner(self):
        known_entities = set()
        
        self.assertFalse(DependencyGraphBuilder.is_valid_dependency_entity("Customer", known_entities))
        self.assertFalse(DependencyGraphBuilder.is_valid_dependency_entity("Internal", known_entities))
        self.assertFalse(DependencyGraphBuilder.is_valid_dependency_entity("Vendor", known_entities))
        self.assertFalse(DependencyGraphBuilder.is_valid_dependency_entity("QA Lead", known_entities))
        
    def test_scenario_4_completed_activity(self):
        candidates = [{
            "activity": "Development",
            "status": "COMPLETED",
            "entity_type": "ACTIVITY",
            "blocked_by": []
        }]
        enriched = ValidationService.enrich_candidates(candidates)
        self.assertEqual(enriched[0]["risk_cat"], "RESOLVED")
        
    def test_scenario_5_scope_request(self):
        candidates = [{
            "activity": "SAP ERP Integration",
            "status": "OPEN",
            "entity_type": "SCOPE_REQUEST",
            "blocked_by": []
        }]
        enriched = ValidationService.enrich_candidates(candidates)
        self.assertEqual(enriched[0]["risk_cat"], "SCOPE_REQUEST")
        
    def test_scenario_1_correct_dependency_chain(self):
        # Production API Credentials -> CRM Integration -> SSO -> SIT -> UAT -> Production
        candidates = [
            {"activity": "Production", "blocked_by": ["UAT"]},
            {"activity": "UAT", "blocked_by": ["SIT"]},
            {"activity": "SIT", "blocked_by": ["SSO"]},
            {"activity": "SSO", "blocked_by": ["CRM Integration"]},
            {"activity": "CRM Integration", "blocked_by": ["Production API Credentials"]},
            {"activity": "Production API Credentials", "blocked_by": []},
        ]
        
        enriched = ValidationService.enrich_candidates(candidates)
        
        enriched_map = {item.get("_canonical_title", item.get("activity")): item for item in enriched}
        
        # Production API Credentials should be root cause and have cascade count 5
        creds = enriched_map.get("Production API Credentials")
        self.assertIsNotNone(creds)
        self.assertTrue(creds.get("is_root_cause"))
        self.assertEqual(creds.get("cascade_depth"), 5)
        self.assertEqual(creds.get("blocked_work_count"), 5)
        # Because this is a single chain, resolving the root cause unlocks the entire chain (5 items)
        self.assertEqual(creds.get("immediate_unlock_count"), 5)
        
        # Production should have 0 cascade depth
        prod = enriched_map.get("Production")
        self.assertIsNotNone(prod)
        self.assertEqual(prod.get("cascade_depth"), 0)

if __name__ == '__main__':
    unittest.main()
