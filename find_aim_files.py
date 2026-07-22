import os
import glob

# Find the AIM folder by walking D:\
for root, dirs, files in os.walk('d:\\'):
    if 'AIM_2020_07_30_16_38_48' in dirs:
        target_dir = os.path.join(root, 'AIM_2020_07_30_16_38_48')
        print('FOUND:', target_dir)
        for f in sorted(os.listdir(target_dir)):
            print(' ', f)
        break
else:
    print('NOT FOUND')
