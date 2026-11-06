# 1. Install LFS (one-time, if not already done)
sudo pacman git-lfs
git lfs install

# 2. Track the screenshots directory recursively (run from repo root)
git lfs track "screenshots/**"
git lfs track "*.png" "*.jpg" "*.gif"


# 3. Commit the .gitattributes file
git add .gitattributes
git commit -m "Track screenshots/ with LFS"

# 4. Add and commit your actual screenshots
git add screenshots/
git commit -m "Add screenshots"
git push

