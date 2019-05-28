# Taken for reference from https://github.com/atbys/elf-packer
import sys
from struct import unpack, pack
import os
import time
import subprocess

def get_c_string(data, offset):
    out = []
    i = offset
    while data[i] != "\x00":
        out.append(data[i])
        i += 1
    return ''.join(out)

def parse_section(data):
    return unpack("IIQQQQIIQQ", data[:64])

def main(argv):
    if len(argv) != 2:
        print "Usage packer <filename>"
        return 1

    with open(argv[1], "rb") as f:
        elf = f.read()

    (
        e_ident, e_type, e_machine, e_version, e_entry, e_phoff, 
        e_shoff, e_flags, e_ehsize, e_phentsize, e_phnum, 
        e_shentsize, e_shnum, e_shstrndx
    ) =  unpack("16sHHIQQQIHHHHHH", elf[:64])
   
    oep = e_entry

    print hex(e_shoff), e_shnum

    sections = []
    strtab_section = -1 
    strtab_offset = None

    for i in xrange(e_shnum):
        offset = i * 64
        s = parse_section(elf[e_shoff + offset :e_shoff +offset+64])
        (
            sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size,
            sh_link, sh_info, sh_addralign, sh_entsize
        ) = s
        sections.append(s)

        if sh_type == 3: 
            strtab_section = i
            strtab_offset = sh_offset

    for i in xrange(e_shnum):
        (
            sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size,
            sh_link, sh_info, sh_addralign, sh_entsize
        ) = sections[i]
        name = get_c_string(elf, strtab_offset + sh_name)
        if name == ".text":
            text_section = i
            text_offset = sh_offset
            text_size = sh_size
            print ".text section @ %x of size %u bytes" % (
                    text_offset, text_size)
            break
    
    packed = bytearray(elf)

    #encrypt
    #for i in xrange(text_size):
    #    packed[text_offset + i] ^= 0xa5
    #for i in xrange(0x100):
    #    packed[0xa0584 + i] = 0xcc       

    magic_offset = 0xa0584
    subprocess.check_output(["nasm", "loader.nasm"])

    with open(argv[1] + ".packed", "wb") as f:
        f.write(packed)


if __name__ == "__main__":
    sys.exit(main(sys.argv))


