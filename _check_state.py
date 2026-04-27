path = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
content = open(path, encoding="utf-8").read()
print("file size:", len(content))

# check if fix_b was written
idx = content.find("_pixelize_sprite_frame")
print("_pixelize_sprite_frame occurrences:", content.count("_pixelize_sprite_frame"))

# find the mask / sprite loop section
idx2 = content.find("guide_baking_albedo")
print("guide_baking_albedo occurrences:", content.count("guide_baking_albedo"))
if idx2 >= 0:
    print(repr(content[idx2:idx2+500]))
