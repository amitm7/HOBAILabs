"""Generate AI images for missing story frames using DALL-E 3."""
import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv(override=True)

client = OpenAI()

frames = [
    {
        "filename": "05_fraud_discovery.jpg",
        "prompt": (
            "Cinematic portrait of a young Indian woman, Assamese features, sitting alone "
            "in a sparse room, head slightly bowed, expression of quiet devastation and betrayal, "
            "holding crumpled papers in her lap. Soft window light, muted warm tones, "
            "shallow depth of field, film grain. Vertical 9:16 composition. "
            "Photorealistic, emotional, no text."
        ),
    },
    {
        "filename": "06_lockdown_youtube.jpg",
        "prompt": (
            "Cinematic shot of a young Indian woman sitting cross-legged on a simple bed "
            "in a modest Indian bedroom, recording herself on a phone propped on a small tripod. "
            "Warm dim lamp light, curtains drawn, lockdown atmosphere, posters on wall behind her. "
            "She looks focused and determined. Vertical 9:16 composition. "
            "Photorealistic, intimate, cinematic lighting, no text."
        ),
    },
]

dest = "/Users/amitmishra/Documents/HOBAILabs/surabhi_assets"

for frame in frames:
    print(f"Generating: {frame['filename']}...")
    response = client.images.generate(
        model="gpt-image-1",
        prompt=frame["prompt"],
        size="1024x1536",
        quality="high",
        n=1,
    )
    import base64
    img_data = base64.b64decode(response.data[0].b64_json)
    out_path = os.path.join(dest, frame["filename"])
    with open(out_path, "wb") as f:
        f.write(img_data)
    print(f"  Saved → {out_path}")

print("\nAll AI frames generated.")
