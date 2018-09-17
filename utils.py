import os


def list_all_file_name_in_dir(path):
    l = []
    for name in os.listdir(path):
        p = os.path.join(path, name)
        if os.path.isdir(p):
            l += list_all_file_name_in_dir(p)
        else:
            l.append(name)
    return l