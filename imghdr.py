"""
Compatibility module for imghdr which was removed in Python 3.13
This provides basic image type detection functionality
"""

import os
import struct

def what(file, h=None):
    """
    Recognize image file formats based on their magic numbers.
    Returns a string describing the image type, or None if the file is not recognized.
    """
    if h is None:
        if isinstance(file, str):
            f = open(file, 'rb')
            h = f.read(32)
            f.close()
        else:
            location = file.tell()
            h = file.read(32)
            file.seek(location)
    
    if len(h) >= 32:
        if h[:8] == b'\x89PNG\r\n\x1a\n':
            return 'png'
        if h[:2] == b'\xff\xd8':
            return 'jpeg'
        if h[:4] == b'GIF8':
            return 'gif'
        if h[:4] == b'RIFF' and h[8:12] == b'WEBP':
            return 'webp'
        if h[:2] == b'BM':
            return 'bmp'
        if h[:4] == b'\x00\x00\x01\x00':
            return 'ico'
        if h[:4] == b'\x00\x00\x02\x00':
            return 'cur'
        if h[:4] == b'II*\x00':
            return 'tiff'
        if h[:4] == b'MM\x00*':
            return 'tiff'
    
    return None

def tests():
    """Test function for the module"""
    import sys
    recursive = 0
    if sys.argv[1:] and sys.argv[1] == '-r':
        del sys.argv[1:2]
        recursive = 1
    try:
        if sys.argv[1:]:
            testall(sys.argv[1:], recursive, 1)
        else:
            testall(['.'], recursive, 1)
    except KeyboardInterrupt:
        sys.stderr.write('\n[Interrupted]\n')
        sys.exit(1)

def testall(filenames, recursive, toplevel):
    """Test all files in the given list"""
    import os
    for filename in filenames:
        if os.path.isdir(filename):
            print(filename + '/:', end=' ')
            if recursive or toplevel:
                print('recursing down:')
                import glob
                names = glob.glob(os.path.join(filename, '*'))
                testall(names, recursive, 0)
            else:
                print('*** directory (use -r) ***')
        else:
            print(filename + ':', end=' ')
            print(what(filename) or '*** not recognized ***')

if __name__ == '__main__':
    tests()
