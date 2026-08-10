import subprocess

with open("test_out.txt", "w", encoding="utf-8") as f:
    process = subprocess.Popen(["python", "test_eval.py"], stdout=f, stderr=subprocess.STDOUT)
    process.wait()
