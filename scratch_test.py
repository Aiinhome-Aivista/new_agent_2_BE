import sys
import traceback

def check_imports():
    try:
        import services.scope_section_detector
        import services.section_dispatcher
        import services.extractors.stakeholder_extractor
        import services.extractors.deliverable_extractor
        import services.entity_resolver
        import services.extractors.functional_scope_extractor
        import api.routes.baseline
        print("All imports successful!")
    except Exception as e:
        print("Import error detected:")
        traceback.print_exc()

if __name__ == "__main__":
    check_imports()
