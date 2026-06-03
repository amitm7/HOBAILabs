"""
Pre-defined cinematic scene designs for Surabhi's story.
Each frame has: emotion, motion_prompt (for Kling), image_prompt (for missing frames).
"""

SURABHI_SCENES = {
    "f01": {
        "emotion": "dignity",
        "motion_prompt": "Auto rickshaw moving through dusty morning street, engine vibration, golden hour light, slow camera pan following the auto",
        "image_prompt": "Cinematic shot of an older Indian man driving a yellow-black auto rickshaw through a narrow Guwahati street at golden hour, shot from inside the auto looking at him from behind, dust particles in warm light, bokeh background of street life, Assamese neighborhood, photorealistic, vertical 9:16",
    },
    "f02": {
        "emotion": "humble_beginnings",
        "motion_prompt": "Slow gentle zoom out revealing a modest home, ambient natural movement, warm nostalgic tone",
        "image_prompt": "Real-feeling vintage photograph aesthetic, Indian woman in a mehndi-colored saree sweeping a small room with a jhadu, one bed one table visible, modest 1RK in Guwahati, soft morning light through a small window, photorealistic, emotional, vertical 9:16",
    },
    "f03": {
        "emotion": "innocent_hope",
        "motion_prompt": "Very slow push-in on children's faces, soft bokeh, warm afternoon light, gentle sway",
        "image_prompt": "Candid-style photograph of Indian children sitting on worn stone steps outside a modest brick home in Assam, smiling naturally, afternoon sunlight, analog film grain, faded colors, nostalgic 2000s photography style, photorealistic, vertical 9:16",
    },
    "f04": {
        "emotion": "determined_hope",
        "motion_prompt": "Slow dolly in on young woman in performance makeup, candlelight flicker, background crowd blurred",
        "image_prompt": "Young Assamese woman in her teens in traditional Bihu performance makeup and attire, bindi, flower in hair, backstage of a small cultural event, warm low light, nervous yet hopeful expression, photorealistic, vertical 9:16",
    },
    "f05": {
        "emotion": "betrayal",
        "motion_prompt": "Camera holds still, subject breathes slowly, eyes well up, very subtle rack focus",
        "image_prompt": "Extreme close-up of a young Assamese Indian woman's face, eyes glistening with unshed tears, bindi, looking slightly downward, harsh afternoon light through a window casting shadows, crumpled paper in background (out of focus), raw emotional portrait, photorealistic, vertical 9:16",
    },
    "f06": {
        "emotion": "quiet_determination",
        "motion_prompt": "Slow push-in from wide to medium, girl teaching children, warm light through window, children's pencils moving",
        "image_prompt": "Young Indian woman seen from behind sitting cross-legged on a floor mat, two young boys in front of her writing in notebooks, modest home tuition setting in Guwahati, warm late afternoon light, simple room, Assamese calendar on wall, photorealistic, vertical 9:16",
    },
    "f07": {
        "emotion": "creative_spark",
        "motion_prompt": "Phone screen glows in dim room, girl adjusts tripod, slow zoom in toward the phone camera lens",
        "image_prompt": "Young Assamese Indian woman in simple home clothes sitting on a bed in a modest lockdown bedroom, phone mounted on a small tripod in front of her, warm dim lamp light, curtains drawn, determined expression, small room with minimal furniture, photorealistic, vertical 9:16",
    },
    "f08": {
        "emotion": "triumph",
        "motion_prompt": "Slow cinematic zoom in on actress face, warm studio lights, hair moving slightly, confident smile",
        "image_prompt": None,  # real photo exists (Nima Denzongpa promo)
    },
    "f09": {
        "emotion": "grief",
        "motion_prompt": "Static hold on framed family photo, very slow zoom in, dust motes in warm light, melancholic",
        "image_prompt": None,  # real photo exists (with father)
    },
    "f10": {
        "emotion": "silent_love",
        "motion_prompt": "Slow zoom out from family portrait, gentle ambient movement, warm golden light",
        "image_prompt": None,  # real photo exists (family)
    },
    "f11": {
        "emotion": "fulfillment",
        "motion_prompt": "Slow gentle push-in, mother and daughter smiling, trees swaying lightly in background breeze",
        "image_prompt": None,  # real photo exists (Taj Mahal with Maa)
    },
    "f12": {  # bonus if we add Paris frame
        "emotion": "aspiration",
        "motion_prompt": "Camera slowly rises up revealing Eiffel Tower, girl spreads arms, natural wind in hair",
        "image_prompt": None,
    },
}
