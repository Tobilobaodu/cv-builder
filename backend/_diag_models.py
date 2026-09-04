"""Diagnostic: locate Docling/HF model cache inside the worker container."""
import os


def walk(root):
    try:
        for dp, dn, fn in os.walk(root):
            for f in fn:
                low = f.lower()
                if "safetensors" in low or "layout" in low or "tableformer" in low or "onnx" in low:
                    print(os.path.join(dp, f))
    except Exception:
        pass


# Common HF/Docling cache locations
for root in ["/root/.cache", "/home", "/usr/local/lib/python3.11/site-packages/docling", "/app"]:
    walk(root)