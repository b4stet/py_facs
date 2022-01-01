from setuptools import setup, find_packages

with open("requirements.txt", encoding="utf-8") as f:
    requirements = f.read().splitlines()

entrypoints = """
[console_scripts]
py_fair=fair.cli:cli
"""

data = {
    'fair': ['data/*.yaml'],
}

setup(
    name='py_fair',
    version='0.1',
    author="b4stet",
    description="Forensic Automation and Cheat Sheets",
    url="https://github.com/b4stet/py_fair",
    packages=find_packages(),
    python_requires=">=3.6",
    install_requires=requirements,
    package_data=data,
    entry_points=entrypoints,
)
