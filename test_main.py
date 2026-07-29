try:
    from main import app
    print("App imported successfully.")
except Exception as e:
    import traceback
    traceback.print_exc()
