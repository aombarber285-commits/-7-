from pathlib import Path
import shutil
import py_compile

src = Path("/mnt/data/SIGZY_BRAIN_V6_0_3BRAIN.py")
dst = Path("/mnt/data/SIGZY_BRAIN_V6_0_TH_TIME.py")

shutil.copy2(src, dst)
py_compile.compile(str(dst), doraise=True)

print(f"สร้างไฟล์ V6 ตัวรันจริงแล้ว: {dst}")
print(f"ตรวจ syntax ผ่าน: {dst.name}")
