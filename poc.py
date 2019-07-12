import os
from struct import pack, unpack
from subprocess import check_output

from elf_enums import SymbolType, ELFFileType
from elf_parser import ELF

BYTES_TO_SAVE = 15
TABLE_ENTRY_SIZE = 32
XOR = 0xa5
PREAMBLE_BYTECODE = bytearray([0x68, 0x00, 0x00, 0x00, 0x00,                # push [table_offset]
                               0x50,                                        # push rax
                               0x48, 0x8d, 0x05, 0x00, 0x00, 0x00, 0x00,    # lea rax, [addr]
                               0xff, 0xe0])                                 # jmp rax


def get_functions_for_encryption(elf):
    func_symbols = [f for f in elf.symbols
                    if f.st_info.st_type == SymbolType.STT_FUNC  # All functions
                    and not f.symbol_name.startswith('__')  # Ignore these functions for whatever reason
                    and f.st_size >= BYTES_TO_SAVE]  # Is bigger than preamble
    return func_symbols


def get_bytes_to_save(elf, function):
    pie_offset = 0x400000 if elf.e_type != ELFFileType.ET_DYN else 0
    data = elf.data[function.st_value - pie_offset:function.st_value + BYTES_TO_SAVE - pie_offset]
    padding = bytearray(b'\0' * (16 - BYTES_TO_SAVE))
    data[len(data):] = padding
    return data


def create_table_entry(bytes_to_save, st_size, func_addr):
    entry = bytearray(TABLE_ENTRY_SIZE)
    entry[0:16] = bytes_to_save
    entry[16:24] = unpack("8B", pack("Q", st_size))
    entry[24:] = unpack("8B", pack("Q", func_addr))

    return entry


def encrypt_and_store_first_bytes(elf, function):
    # Save the original bytes for later
    bytes_to_save = get_bytes_to_save(elf, function)
    table_entry = create_table_entry(bytes_to_save, function.st_size, function.st_value)

    # Encrypt the function's data
    pie_offset = 0x400000 if elf.e_type != ELFFileType.ET_DYN else 0
    start = function.st_value - pie_offset
    end = start + function.st_size

    for j in range(start, end):
        elf.data[j] = elf.data[j] ^ XOR

    return table_entry


def write_new_preamble(elf, index, function, decrypt):
    offset = pack('I', index)
    pie_offset = 0x400000 if elf.e_type != ELFFileType.ET_DYN else 0
    jump = pack('I', (decrypt - pie_offset) - (function.st_value - pie_offset) - 5)

    bytecode = PREAMBLE_BYTECODE.copy()
    bytecode[1:5] = offset
    bytecode[9:13] = jump

    # Replace the function preamble with the new bytes
    start = function.st_value - pie_offset
    end = start + BYTES_TO_SAVE
    assert (BYTES_TO_SAVE == len(bytecode))
    elf.data[start:end] = bytecode


def get_preamble_bytes(decrypt, index, nasm):
    tmp_file = "tmp/preamble"
    tmp_src = f"{tmp_file}.nasm"

    # Replace the assembly with the relevant values
    asm = open(nasm, 'r').read()
    asm = asm.replace("#OFFSET#", f"{index}") \
        .replace("#DEC_FUNC#", f"{hex(decrypt)}")

    # Create a temp file with assembled instructions
    with open(tmp_src, 'w') as f:
        f.write(asm)

    check_output(['nasm', '-O0', tmp_src])

    # Get the assembled bytes
    bytecode = bytearray(open(tmp_file, 'rb').read())

    # Delete temp files
    os.remove(tmp_file)
    os.remove(tmp_src)

    return bytecode


def load_loader(elf, nop, loader_asm):
    # Assemble the loader
    output = f"{loader_asm}.out"
    check_output(['nasm', loader_asm, '-o', output])

    # Replace the bytes in the nop function
    loader_bytes = bytearray(open(output, 'rb').read())

    start = nop.p_offset  # - PIE_OFFSET
    end = start + len(loader_bytes)
    elf.data[start:end] = loader_bytes

    # Remove the assembled file
    os.remove(output)


def main(filename):
    elf = ELF(filename)
    loader_asm = 'asm/loader.nasm'
    loader = open(loader_asm, 'r').read()

    # Find address of all functions to be encrypted
    #functions = get_functions_for_encryption(elf)
    functions = [x for x in elf.symbols if
                 x.symbol_name == 'main' or
                 x.symbol_name == 'add' or
                 x.symbol_name == 'mul' or
                 x.symbol_name == 'sub' or
                 x.symbol_name == 'no_args']

    # Update function address in the loader
    text_section = [x for x in elf.sections if x.section_name == '.text'][0]
    loader = loader.replace("#TEXT_START#", f"{hex(text_section.sh_addr)}") \
        .replace("#TEXT_LEN#", f"{hex(text_section.sh_size)}") \
        .replace("#OEP#", f"{hex(elf.e_entry)}")

    # Calculate table based on the address and number of those functions
    table = []
    for function in functions:
        table.append(encrypt_and_store_first_bytes(elf, function))

    # Load the bytes for the table at the start of the loader
    table_array = []
    for entry in table:
        table_array.append(','.join('0x{:02x}'.format(x) for x in entry))
    table_str = ','.join(x for x in table_array)
    table_size = len(table_array)

    # Get nop function for overwriting
    # nop = get_nop_function(elf)
    nop = elf.append_loadable_segment(b'\x00' * (table_size * TABLE_ENTRY_SIZE + 400))

    # Calculate address of various functions in loader:
    #  - entry
    #  - decrypt
    #  - encrypt
    entry_addr = nop.p_vaddr
    decrypt = entry_addr + 0x26

    # Load the default preamble bytes into the loader
    preamble = PREAMBLE_BYTECODE.copy()
    loader = loader.replace("#PREAMBLE#", ','.join(f'0x{x:02x}' for x in preamble)) \
        .replace("#BIN_OFFSET#", str(nop.p_vaddr))

    # Prepend the table string to the loader
    loader = loader.replace("#TABLE#", table_str)
    with open(f"{loader_asm}.new", 'w') as f:
        f.write(loader)

    # Add the call to encryption routine at the start of each function, ensuring the correct addresses/offsets are used
    i = 0
    while i < len(functions):
        write_new_preamble(elf, i, functions[i], decrypt)
        i += 1

    # Load in loader to the address of NOP
    load_loader(elf, nop, f"{loader_asm}.new")

    # Change OEP
    # elf.e_entry = entry_addr

    # Make text section writeable
    # TODO: This shouldn't be necessary
    text = [x for x in elf.sections if x.section_name == '.text'][0]
    seg = [x for x in elf.segments if x.p_offset < text.sh_offset <= x.p_offset + x.p_filesz][0]
    seg.p_flags = 7

    # Save the packed elf
    with open(f"{filename}.packed", 'wb') as f:
        f.write(elf.data)

    print(f"Complete - file saved as {filename}.packed")


if __name__ == '__main__':
    main('test/test')
