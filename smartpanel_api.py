#!/usr/bin/env python3

from fastapi import FastAPI
import subprocess
import json
import glob

app = FastAPI(
    title="SmartPanel API",
    version="1.0"
)


def run_command(cmd):

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True
    )

    return {
        "success": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr
    }


@app.get("/")
def root():

    return {
        "service": "SmartPanel API",
        "version": "1.0"
    }


@app.post("/save")
def save():

    return run_command([
        "./save_all.sh"
    ])


@app.post("/check")
def check():

    return run_command([
        "./check_all.sh"
    ])


@app.post("/restore")
def restore():

    return run_command([
        "./restore_all.sh"
    ])


@app.post("/restore-normalize")
def restore_normalize():

    return run_command([
        "./restore_all.sh",
        "--normalize"
    ])


@app.get("/status")
def status():

    panels = []

    for file in glob.glob("metrics/*.json"):

        with open(file) as f:

            panels.append(
                json.load(f)
            )

    return {
        "panelCount": len(panels),
        "panels": panels
    }


@app.get("/health")
def health():

    return {
        "status": "healthy"
    }
