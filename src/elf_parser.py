from struct import unpack, pack

import r2pipe

from elf_enums import *

"""
ELF Specification: http://ftp.openwatcom.org/devel/docs/elf-64-gen.pdf
"""


def is_string_hex(string):
    try:
        int(string, 16)
        return True
    except ValueError:
        return False


def parse_string_data(data, offset):
    string_array = []
    i = offset
    while data[i] != "\x00":
        string_array.append(data[i])
        i += 1

    return ''.join(string_array)


def parse_header(data, entity_number, entsize, h_offset, hdr_struct):
    # entsize is the header size for the entity
    inner_offset = entity_number * entsize
    start_offset = h_offset + inner_offset
    end_offset = start_offset + entsize

    # Extract the header data from the full data
    extract_data = data[start_offset:end_offset]
    return unpack(hdr_struct, extract_data)


def repack_header(data, hdr_offset, hdr_size, hdr, hdr_struct):
    end_offset = hdr_offset + hdr_size
    data[hdr_offset:end_offset] = pack(hdr_struct, *hdr)


class ELFIdent:
    """ e_ident containing identification details of the ELF file """

    def __init__(self, header):
        self.data = header
        self.el_mag = header[0:4]
        assert (self.el_mag == b"\x7fELF")
        self.el_class = ELFClass(header[4])
        self.el_data = ELFData(header[5])
        self.el_version = header[6]
        self.el_osabi = ELFOSABI(header[7])
        self.el_abiversion = header[8]
        self.el_pad = header[9:15]
        self.el_nident = header[15]


class ELF:
    """ File Header
    unsigned char   e_ident[16];    /* ELF identification */
    Elf64_Half      e_type;         /* Object file type */
    Elf64_Half      e_machine;      /* Machine type */
    Elf64_Word      e_version;      /* Object file version */
    Elf64_Addr      e_entry;        /* Entry point address */
    Elf64_Off       e_phoff;        /* Program header offset */
    Elf64_Off       e_shoff;        /* Section header offset */
    Elf64_Word      e_flags;        /* Processor-specific flags */
    Elf64_Half      e_ehsize;       /* ELF header size */
    Elf64_Half      e_phentsize;    /* Size of program header entry */
    Elf64_Half      e_phnum;        /* Number of program header entries */
    Elf64_Half      e_shentsize;    /* Size of section header entry */
    Elf64_Half      e_shnum;        /* Number of section header entries */
    Elf64_Half      e_shstrndx;     /* Section name string table index */
    """

    @property
    def header(self):
        """ Gets the tuple containing the values within the header of the entity """
        return (
            self.e_ident.data,
            self.e_type.value,
            self.e_machine.value,
            self.e_version,
            self._e_entry,
            self._e_phoff,
            self._e_shoff,
            self.e_flags,
            self.e_ehsize,
            self.e_phentsize,
            self._e_phnum,
            self.e_shentsize,
            self._e_shnum,
            self.e_shstrndx
        )

    @property
    def e_entry(self):
        """ Gets or sets the entry point of the program """
        return self._e_entry

    @e_entry.setter
    def e_entry(self, value):
        self._e_entry = value
        self._repack_header()

    @property
    def e_phoff(self):
        """ Gets or sets the program header offset """
        return self._e_phoff

    @e_phoff.setter
    def e_phoff(self, value):
        self._e_phoff = value
        for seg in self.segments:
            seg.e_phoff = value
        self._repack_header()

    @property
    def e_shoff(self):
        """ Gets or sets the section header offset """
        return self._e_shoff

    @e_shoff.setter
    def e_shoff(self, value):
        self._e_shoff = value
        self._repack_header()

    @property
    def e_phnum(self):
        """ Gets or sets the number of program headers """
        return self._e_phnum

    @e_phnum.setter
    def e_phnum(self, value):
        self._e_phnum = value
        self._repack_header()

    @property
    def e_shnum(self):
        """ Gets or sets the number of section headers """
        return self._e_shnum

    @e_shnum.setter
    def e_shnum(self, value):
        self._e_shnum = value
        self._repack_header()

    @property
    def virtual_base(self):
        """ Gets the virtual base of the binary (eg. 0x4000000 if no-pie) """
        return self._virtual_base

    @property
    def linking_method(self):
        """ Gets how the ELF has been linked - dynamically or statically """
        return self._linking_method

    def __init__(self, filename):
        data = open(filename, 'rb').read()
        self.r2 = r2pipe.open(filename)
        self.data = bytearray(data)
        self._full_data = self.data
        self.hdr_struct = "16sHHIQQQIHHHHHH"
        self.hdr_size = 64  # TODO: This is contained in the header as e_ehsize
        self.segments = []
        self.sections = []
        self.symbols = []
        (
            self.e_ident,
            self.e_type,
            self.e_machine,
            self.e_version,
            self._e_entry,
            self._e_phoff,
            self._e_shoff,
            self.e_flags,
            self.e_ehsize,
            self.e_phentsize,
            self._e_phnum,
            self.e_shentsize,
            self._e_shnum,
            self.e_shstrndx
        ) = self._parse_header(self.data, self.hdr_size, self.hdr_struct)

        # Pull the data containing the segment names initially
        shstrtab_data = Section(self.data, self.e_shstrndx, self.e_shoff, self.e_shentsize).data

        # Extract the Segments
        for i in range(self.e_phnum):
            self.segments.append(Segment(self.data, i, self.e_phoff, self.e_phentsize))

        # Extract the Sections
        for i in range(self.e_shnum):
            self.sections.append(Section(self.data, i, self.e_shoff, self.e_shentsize, shstrtab_data))

        # Associate each section with the segment it's contained within
        for s in self.segments:
            s.load_sections(self.sections)

        # Whether the ELF is statically or dynamically linked
        self._linking_method = ELFLinkingMethod(len([x for x in self.sections if x.section_name == '.interp']))

        # Extract the Symbols
        symtab = next(iter([x for x in self.sections if x.section_name == '.symtab']), None)
        strtab = next(iter([x for x in self.sections if x.section_name == '.strtab']), None)

        # Take stripped binaries into account
        if symtab is not None:
            for i in range(int(symtab.sh_size / symtab.sh_entsize)):
                self.symbols.append(Symbol(self.data, i, symtab.sh_offset, symtab.sh_entsize, strtab.data))

        # Associate each symbol with the section it's contained within
        for s in self.sections:
            s.load_symbols(self.symbols)

        self._virtual_base = min(x.p_vaddr for x in self.segments if x.p_type == ProgramType.PT_LOAD)
        self._set_headers()

    def list_functions(self):
        """
        Lists all functions and addresses within the text section fo the binary, using radare2 if it is available on
        the system, otherwise using the symbol table. If neither of those options  are available, the command fails
        as trying to manually extract functions is too error prone.

        :return: A collection of Function objects tuples within the text section and their addresses
        """

        # When symbols are available
        text = self.get_section('.text')

        if len(self.symbols) > 0:
            func_symbols = [Function(fn.symbol_name, fn.st_value, fn.st_size)
                            for fn in text.symbols
                            if fn.st_info.st_type == SymbolType.STT_FUNC]  # All functions
            return func_symbols

        else:
            # Analyse the functions using radare
            try:
                self.r2.cmd('aaa')
                functions = self.r2.cmdJ('aflj')
                return [Function(x.name, x.offset, x.size) for x in functions]

            # If r2 is not on the system, go for the less accurate method of searching the symbols, or call instructions
            except FileNotFoundError:
                raise FileNotFoundError("Radare2 was not found, and the binary is stripped. Cannot extract functions")

    def get_section(self, name):
        """
        Gets a section with the name specified. If more than one section have the name, an error is thrown.

        :param name: The name of the section to return
        :return: A Section object with the name specified
        """
        sections = [x for x in self.sections if x.section_name == name]
        assert (len(sections) == 1)
        return sections[0]

    def get_data_segment(self, start_addr, end_addr):
        """
        Extracts a segment of data as a bytearray.

        :param start_addr: The offset in bytes wtihin the binary where the data to be extracted is found
        :param end_addr: The end address in bytes of the data
        :return: A bytearray containing the data
        """
        return self.data[start_addr:end_addr]

    def append_loadable_segment_3(self, size):
        """
        Appends a new segment of the size specified, modifying any relevant pointers required.

        Due to issues with modifying the program header, this function clobbers the NOTE segment/sections which reside
        after the program header or interp section. If that segment isn't a NOTE segment, issues will occur when
        running the binary.

        :param size: The minimum size to make the segment given required alignments
        :return: A new segment from within the ELF file
        """

        self._shift_interp()

        # Create a new segment
        new_segment = self._create_new_segment(size + self.phdr.p_filesz)
        packed = pack(new_segment.hdr_struct, *new_segment.header)
        self.segments.append(new_segment)

        # Add header entry to end of header section
        offset = self.e_phoff + (self.e_phentsize * self.e_phnum)
        self._full_data[offset:offset + self.e_phentsize] = packed
        self.e_phnum += 1

        return new_segment

    def _shift_interp(self):
        """
        Shifts the INTERP section/segment/symbol in order to make room for a new header section. This overwrites
        any data after this section, which is hopefully unused.
        """
        interp = [s for s in self.segments if s.p_type == ProgramType.PT_INTERP]
        if len(interp) == 0:
            return

        # Get the interp
        interp_seg = [s for s in self.segments if s.p_type == ProgramType.PT_INTERP][0]
        interp_sym = [s for s in self.symbols if s.st_value == interp_seg.p_vaddr][0]

        interp_seg.p_offset += self.e_phentsize
        interp_seg.p_vaddr += self.e_phentsize
        interp_seg.p_paddr += self.e_phentsize

        interp_sec = self.get_section('.interp')
        interp_sec.sh_offset += self.e_phentsize
        interp_sec.sh_addr += self.e_phentsize

        interp_sym.st_value += self.e_phentsize

        # Move the interp data over the notes section
        start = interp_seg.p_offset
        end = interp_seg.p_offset + interp_seg.p_filesz
        assert (end - start == len(interp_seg.data))
        self.data[start:end] = interp_seg.data

    def append_loadable_segment_2(self, size):

        # Get the segment loading the program header
        phdr_segment = [s for s in self.segments
                        if s.p_type == ProgramType.PT_LOAD
                        and s.p_offset <= self.phdr.p_offset
                        and s.p_offset + s.p_filesz >= self.phdr.p_offset + self.phdr.p_filesz][0]

        # Increase the size of the segment to account for the new segment being added
        phdr_segment.p_filesz += self.e_phentsize
        phdr_segment.p_memsz += self.e_phentsize

        # Shift any segments after the program header over by the size of an entry
        marker = self.phdr.p_offset + self.phdr.p_filesz
        self._shift_data(marker)
        self._shift_segments(marker)
        self._shift_sections(marker)

        # Create new segment
        new_segment = self._create_new_segment(size)
        packed = pack(new_segment.hdr_struct, *new_segment.header)
        self.segments.append(new_segment)

        # Add header to end of header section
        offset = self.e_phoff + (self.e_phentsize * self.e_phnum)
        self._full_data[offset:offset + self.e_phentsize] = packed
        self.e_phnum += 1

        return new_segment

    def _shift_data(self, marker):
        init_marker = marker
        next_segment = [s for s in self.segments if init_marker <= s.p_offset <= init_marker + self.e_phentsize][0]
        moved_segments = []
        p_offset = next_segment.p_offset
        while next_segment is not None:
            moved_segments.append(next_segment)
            marker = next_segment.p_offset + next_segment.p_filesz
            p_offset += self.e_phentsize
            try:
                next_segment = [s for s in self.segments
                                if marker <= s.p_offset <= marker + self.e_phentsize
                                and s not in moved_segments][0]
            except IndexError:
                next_segment = None

        moved_sections = []
        next_section = [s for s in self.sections if init_marker <= s.sh_offset <= init_marker + self.e_phentsize][0]
        sh_offset = next_section.sh_offset
        while next_section is not None:
            moved_sections.append(next_section)
            marker = next_section.sh_offset + next_section.sh_size
            sh_offset += self.e_phentsize
            try:
                next_section = [s for s in self.sections
                                if marker <= s.sh_offset <= marker + self.e_phentsize
                                and s not in moved_sections][0]
            except IndexError:
                next_section = None

        max_end = init_marker
        for segment in moved_segments:
            seg_end = segment.p_offset + segment.p_filesz
            max_end = seg_end if max_end < seg_end else max_end

        for section in moved_sections:
            sec_end = section.sh_offset + section.sh_size
            max_end = sec_end if max_end < sec_end else max_end

        self.data[init_marker+self.e_phentsize:max_end+self.e_phentsize] = self.data[init_marker:max_end]

    def _shift_segments(self, marker):
        next_segment = [s for s in self.segments if marker <= s.p_offset <= marker + self.e_phentsize][0]
        moved_segments = []
        while next_segment is not None:
            moved_segments.append(next_segment)
            marker = next_segment.p_offset + next_segment.p_filesz
            next_segment.p_offset += self.e_phentsize
            next_segment.p_vaddr += self.e_phentsize
            try:
                next_segment = [s for s in self.segments
                                if marker <= s.p_offset <= marker + self.e_phentsize
                                and s not in moved_segments][0]
            except IndexError:
                next_segment = None

        # for segment in (sorted(moved_segments, key=lambda s: s.p_offset, reverse=True)):
        #     self.data[segment.p_offset:segment.p_offset + segment.p_filesz] = segment.data

    def _shift_sections(self, marker):
        next_section = [s for s in self.sections if marker <= s.sh_offset <= marker + self.e_phentsize][0]
        moved_sections = []
        while next_section is not None:
            moved_sections.append(next_section)
            marker = next_section.sh_offset + next_section.sh_size
            next_section.sh_offset += self.e_phentsize
            next_section.sh_addr += self.e_phentsize
            try:
                next_section = [s for s in self.sections
                                if marker <= s.sh_offset <= marker + self.e_phentsize
                                and s not in moved_sections][0]
            except IndexError:
                next_section = None


    def _create_new_segment(self, size):
        """
        Creates a new Segment object at the correct alignment/offset within the binary and populates the header with
        the correct values and data. This also appends the data into the correct place after the binary's data.
        of the ELF binary.

        :param size: The length of the data to load into the Segment
        :return: A new Segment object with the data/header values populated
        """

        last_segment = sorted(self.segments, key=lambda x: x.p_offset + x.p_filesz)[-1]
        end_addr = len(self.data)

        # Segment header values - offset at the specific alignment
        p_type = ProgramType.PT_LOAD
        p_flags = 0x5
        p_align = 0x1000
        addr_space = 0xffffffffffffffff

        # Make sure the segment is located at the correct alignment
        bitmask = addr_space ^ abs((1 - p_align))
        p_offset = (end_addr + p_align) & bitmask
        p_vaddr = (last_segment.p_vaddr + last_segment.p_memsz + p_align) & bitmask
        p_paddr = (last_segment.p_paddr + last_segment.p_memsz + p_align) & bitmask

        # Add padding to the alignment
        pad_len = p_offset - end_addr
        self.data[end_addr:p_offset] = b'\x00' * pad_len

        # Add data to elf
        self.data[p_offset:p_offset] = b'\x00' * size

        header = (
            p_type,
            p_flags,
            p_offset,
            p_vaddr,
            p_paddr,
            size,  # p_filesz
            size,  # p_memsz
            p_align
        )
        return Segment(self._full_data, self.e_phnum, self.e_phoff, self.e_phentsize, header)

    @staticmethod
    def _parse_header(data, header_size, hdr_struct):
        """
        Parses the bytearray data to extract the header values.

        :param data: The bytearray containing the data of the binary.
        :param header_size: The size of the ELF header in bytes.
        :param hdr_struct: The code used to parse the header within struct.pack()
        :return: A set of ELF header values for the current binary.
        """
        header = unpack(hdr_struct, data[:header_size])
        return (
            ELFIdent(header[0]),  # e_ident
            ELFFileType(header[1]),  # e_type
            ELFMachine(header[2]),  # e_machine
            header[3],  # e_version
            header[4],  # e_entry
            header[5],  # e_phoff
            header[6],  # e_shoff
            header[7],  # e_flags
            header[8],  # e_ehsize
            header[9],  # e_phentsize
            header[10],  # e_phnum
            header[11],  # e_shentsize
            header[12],  # e_shnum
            header[13],  # e_shstrndx
        )

    @staticmethod
    def _extract_header_names(data):
        return [x for x in data.decode("utf-8").split('\0')]

    def _set_headers(self):
        """
        Sets the program header and section header as properties within the ELF object.
        """

        # Program Header
        if self.linking_method == ELFLinkingMethod.DYNAMIC:
            self.phdr = [x for x in self.segments if x.p_type == ProgramType.PT_PHDR][0]
        else:
            # Create a new "ghost" segment containing the correct data, without adding it to the segment list
            header = (
                ProgramType.PT_PHDR,              # p_type
                4,                                # p_flags
                self.e_phoff,                     # p_offset
                self.e_phoff,                     # p_vaddr
                self.e_phoff,                     # p_paddr
                self.e_phentsize * self.e_phnum,  # p_filesz
                self.e_phentsize * self.e_phnum,  # p_memsz
                8                                 # p_align
            )
            self.phdr = Segment(self._full_data, 0, self.e_phoff, self.e_phentsize, header)

        # TODO: Section Header

    def _repack_header(self):
        """ Re-packs the header once edits have been made, so as to propogate any changes within the binary data """
        repack_header(self._full_data, 0, self.e_ehsize, self.header, self.hdr_struct)


class Segment:
    """ Program Header
    Elf64_Word      p_type;         /* Type of segment */
    Elf64_Word      p_flags;        /* Segment attributes */
    Elf64_Off       p_offset;       /* Offset in file */
    Elf64_Addr      p_vaddr;        /* Virtual address in memory */
    Elf64_Addr      p_paddr;        /* Reserved */
    Elf64_Xword     p_filesz;       /* Size of segment in file */
    Elf64_Xword     p_memsz;        /* Size of segment in memory */
    Elf64_Xword     p_align;        /* Alignment of segment */
    """

    @property
    def header(self):
        """ Gets the tuple containing the values within the header of the entity """
        return (
            self._p_type.value,
            self._p_flags,
            self._p_offset,
            self._p_vaddr,
            self._p_paddr,
            self._p_filesz,
            self._p_memsz,
            self._p_align
        )

    @property
    def p_type(self):
        """ Gets or sets the type of segment """
        return self._p_type

    @p_type.setter
    def p_type(self, value):
        self._p_type = value
        self._repack_header()

    @property
    def p_flags(self):
        """ Gets or sets the flags of the segment """
        return self._p_flags

    @p_flags.setter
    def p_flags(self, value):
        self._p_flags = value
        self._repack_header()

    @property
    def p_offset(self):
        """ Gets or sets the offset of the program header within the file """
        return self._p_offset

    @p_offset.setter
    def p_offset(self, value):
        self._p_offset = value
        self._repack_header()

    @property
    def p_vaddr(self):
        """ Gets or sets the virtual address of the segment within memory """
        return self._p_vaddr

    @p_vaddr.setter
    def p_vaddr(self, value):
        self._p_vaddr = value
        self._repack_header()

    @property
    def p_paddr(self):
        """ Gets or sets some reserved bytes in memory """
        return self._p_paddr

    @p_paddr.setter
    def p_paddr(self, value):
        self._p_paddr = value
        self._repack_header()

    @property
    def p_filesz(self):
        """ Gets or sets the size of the segment within the file """
        return self._p_filesz

    @p_filesz.setter
    def p_filesz(self, value):
        self._p_filesz = value
        self._repack_header()

    @property
    def p_memsz(self):
        """ Gets or sets the size of the segment within the memory """
        return self._p_memsz

    @p_memsz.setter
    def p_memsz(self, value):
        self._p_memsz = value
        self._repack_header()

    @property
    def p_align(self):
        """ Gets or sets the alignment of the segment - must be a power of 2, with p_offset and p_vaddr
        congruent modulo the alignment """
        return self._p_align

    @p_align.setter
    def p_align(self, value):
        self._p_align = value
        self._repack_header()

    @property
    def sections(self):
        """ Gets the collection of sections contained within the segment"""
        return self._sections

    def __init__(self, data, segment_number, e_phoff, e_phentsize, header=None):
        self._full_data = data
        self.hdr_struct = "IIQQQQQQ"
        self.e_phoff = e_phoff
        self.e_phentsize = e_phentsize
        self.segment_number = segment_number
        self._sections = []
        if header is not None:
            self._set_header(header)
            # self._repack_header()
        else:
            (
                self._p_type,
                self._p_flags,
                self._p_offset,
                self._p_vaddr,
                self._p_paddr,
                self._p_filesz,
                self._p_memsz,
                self._p_align
            ) = self._parse_header(data, segment_number)

        # Extract raw data
        self.data = data[self.p_offset:self.p_offset + self.p_filesz]

    def load_sections(self, sections):
        """
        Parses a list of sections and adds them to the local collection if they are contained within
        the address range of the current segment.

        :param sections: The full collection of sections to check.
        """
        relevant_sections = [x for x in sections
                             if x.sh_offset >= self.p_offset
                             and x.sh_offset + x.sh_size <= self.p_offset + self.p_filesz]
        self._sections.extend(relevant_sections)

    def is_segment_gap_overlapped(self, closest_segment, segments):
        """
        Checks whether a gap between this segment and the next closest one is overlapped by any other segment in
        a list. This covers situations whereby a segment either completely overlaps the gap, ends in the middle,
        or starts in the middle.

        :param closest_segment: The segment next to this with a gap of null bytes between them.
        :param segments: A list of segments to check through
        :return: True if there is an overlapping segment, otherwise false.
        """
        gap_start = self.p_offset + self.p_filesz  # The end of the current segment
        gap_end = closest_segment.p_offset  # The start of the next segment

        for segment in segments:

            # Don't need to check the current segments
            if segment is self or segment is closest_segment:
                continue

            segment_end = segment.p_offset + segment.p_filesz

            # Check segments within the gap
            if gap_start <= segment.p_offset <= gap_end \
                    or gap_start <= segment_end <= gap_end:
                return True

            # Check segments overlapping the gap entirely
            if segment.p_offset <= gap_start and segment_end >= gap_end:
                return True

        return False

    def __str__(self):
        return f"{self.p_type} @ {hex(self.p_offset)}"

    def _set_header(self, header):
        self._p_type = header[0]
        self._p_flags = header[1]
        self._p_offset = header[2]
        self._p_vaddr = header[3]
        self._p_paddr = header[4]
        self._p_filesz = header[5]
        self._p_memsz = header[6]
        self._p_align = header[7]

    def _parse_header(self, data, segment_number):
        header = parse_header(data, segment_number, self.e_phentsize, self.e_phoff, self.hdr_struct)
        return (
            ProgramType(header[0]),
            header[1],
            header[2],
            header[3],
            header[4],
            header[5],
            header[6],
            header[7]
        )

    def _repack_header(self):
        offset = self.e_phoff + (self.segment_number * self.e_phentsize)
        repack_header(self._full_data, offset, self.e_phentsize, self.header, self.hdr_struct)


class Section:
    """ Section Header
    Elf64_Word      sh_name;        /* Section name */
    Elf64_Word      sh_type;        /* Section type */
    Elf64_Xword     sh_flags;       /* Section attributes */
    Elf64_Addr      sh_addr;        /* Virtual address in memory */
    Elf64_Off       sh_offset;      /* Offset in file */
    Elf64_Xword     sh_size;        /* Size of section */
    Elf64_Word      sh_link;        /* Link to other section */
    Elf64_Word      sh_info;        /* Miscellaneous information */
    Elf64_Xword     sh_addralign;   /* Address alignment boundary */
    Elf64_Xword     sh_entsize;     /* Size of entries, if section has table */
    """

    @property
    def header(self):
        """ Gets the tuple containing the values within the header of the entity """
        return (
            self._sh_name,
            self._sh_type.value,
            self._sh_flags,
            self._sh_addr,
            self._sh_offset,
            self._sh_size,
            self._sh_link,
            self._sh_info,
            self._sh_addralign,
            self._sh_entsize
        )

    @property
    def sh_name(self):
        """ Gets or sets the name of section """
        return self._sh_name

    @sh_name.setter
    def sh_name(self, value):
        self._sh_name = value
        self._repack_header()

    @property
    def sh_type(self):
        """ Gets or sets the type of section """
        return self._sh_type

    @sh_type.setter
    def sh_type(self, value):
        self._sh_type = value
        self._repack_header()

    @property
    def sh_flags(self):
        """ Gets or sets the RWX flags of the section """
        return self._sh_flags

    @sh_flags.setter
    def sh_flags(self, value):
        self._sh_flags = value
        self._repack_header()

    @property
    def sh_addr(self):
        """ Gets or sets the virtual address in memory of the section """
        return self._sh_addr

    @sh_addr.setter
    def sh_addr(self, value):
        self._sh_addr = value
        self._repack_header()

    @property
    def sh_offset(self):
        """ Gets or sets the offset in bytes within the file of the section """
        return self._sh_offset

    @sh_offset.setter
    def sh_offset(self, value):
        self._sh_offset = value
        self._repack_header()

    @property
    def sh_size(self):
        """ Gets or sets the size in bytes within the file of the section """
        return self._sh_size

    @sh_size.setter
    def sh_size(self, value):
        self._sh_size = value
        self._repack_header()

    @property
    def sh_link(self):
        """ Gets or sets the link to the next section if relevant """
        return self._sh_link

    @sh_link.setter
    def sh_link(self, value):
        self._sh_link = value
        self._repack_header()

    @property
    def sh_info(self):
        """ Gets or sets miscellaneous information about the section """
        return self._sh_info

    @sh_info.setter
    def sh_info(self, value):
        self._sh_info = value
        self._repack_header()

    @property
    def sh_addralign(self):
        """ Gets or sets the address alignment boundary of the section """
        return self._sh_addralign

    @sh_addralign.setter
    def sh_addralign(self, value):
        self._sh_addralign = value
        self._repack_header()

    @property
    def sh_entsize(self):
        """ Gets or sets the size of the entries if the section has a table """
        return self._sh_entsize

    @sh_entsize.setter
    def sh_entsize(self, value):
        self._sh_entsize = value
        self._repack_header()

    @property
    def symbols(self):
        """ Gets the collection of symbols that point to references within the section """
        return self._symbols

    def __init__(self, data, section_number, e_shoff, e_shentsize, header_names=None):
        self._full_data = data
        self.hdr_struct = "IIQQQQIIQQ"
        self.e_shoff = e_shoff  # Section header offset
        self.e_shentsize = e_shentsize
        self.section_number = section_number
        self._symbols = []
        (
            self._sh_name,
            self._sh_type,
            self._sh_flags,
            self._sh_addr,
            self._sh_offset,
            self._sh_size,
            self._sh_link,
            self._sh_info,
            self._sh_addralign,
            self._sh_entsize
        ) = self._parse_header(data, section_number)

        # Get the name of the section
        if header_names is not None:
            self.section_name = parse_string_data(header_names.decode('utf-8'), self.sh_name)

        # Extract raw data
        self.data = data[self.sh_offset:self.sh_offset + self.sh_size]

    def load_symbols(self, symbols):
        """
        Parses a list of symbols and adds them to the local collection if they are contained within
        the address range of the current section.

        :param symbols: The full collection of symbols to check.
        """
        relevant_symbols = [x for x in symbols
                            if x.st_value >= self.sh_addr
                            and x.st_value + x.st_size <= self.sh_addr + self.sh_size]
        self._symbols.extend(relevant_symbols)

    def __str__(self):
        return f"{self.section_name} @ {hex(self.sh_offset)}"

    def _parse_header(self, data, section_number):
        header = parse_header(data, section_number, self.e_shentsize, self.e_shoff, self.hdr_struct)
        return (
            header[0],
            SectionType(header[1]),
            header[2],
            header[3],
            header[4],
            header[5],
            header[6],
            header[7],
            header[8],
            header[9],
        )

    def _repack_header(self):
        offset = self.e_shoff + (self.section_number * self.e_shentsize)
        repack_header(self._full_data, offset, self.e_shentsize, self.header, self.hdr_struct)


class Symbol:
    """ Symbol Header
    Elf64_Word      st_name;        /* Symbol name */
    unsigned char   st_info;        /* Type and Binding attributes */
    unsigned char   st_other;       /* Reserved */
    Elf64_Half      st_shndx;       /* Section table index */
    Elf64_Addr      st_value;       /* Symbol value */
    Elf64_Xword     st_size;        /* Size of object (e.g., common) */
    """

    @property
    def header(self):
        """ Gets the tuple containing the values within the header of the entity """
        return (
            self._st_name,
            self._st_info.get_value(),
            self._st_other,
            self._st_shndx,
            self._st_value,
            self._st_size
        )

    @property
    def st_name(self):
        """ Gets or sets the name of symbol """
        return self._st_name

    @st_name.setter
    def st_name(self, value):
        self._st_name = value
        self._repack_header()

    @property
    def st_info(self):
        """ Gets or sets the symbol type and binding attributes """
        return self._st_info

    @st_info.setter
    def st_info(self, value):
        self._st_info = value
        self._repack_header()

    @property
    def st_other(self):
        """ Gets or sets the reserved bit (must be zero)"""
        return self._st_other

    @st_other.setter
    def st_other(self, value):
        self._st_other = value
        self._repack_header()

    @property
    def st_shndx(self):
        """ Gets or sets the index of the section which the symbol is defined in (if available) """
        return self._st_shndx

    @st_shndx.setter
    def st_shndx(self, value):
        self._st_shndx = value
        self._repack_header()

    @property
    def st_value(self):
        """ Gets or sets the value of the symbol, either absolute or relocatable address """
        return self._st_value

    @st_value.setter
    def st_value(self, value):
        self._st_value = value
        self._repack_header()

    @property
    def st_size(self):
        """ Gets or sets the size of the symbol if available """
        return self._st_size

    @st_size.setter
    def st_size(self, value):
        self._st_size = value
        self._repack_header()

    def __init__(self, data, symbol_number, sh_offset, sh_entsize, header_names):
        self._full_data = data
        self.hdr_struct = "IbbHQQ"
        self.symbol_number = symbol_number
        self.sh_offset = sh_offset
        self.sh_entsize = sh_entsize
        (
            self._st_name,
            self._st_info,
            self._st_other,
            self._st_shndx,
            self._st_value,
            self._st_size
        ) = self._parse_header(data, symbol_number)

        # Get the name of the symbol
        if self.st_name > 0:
            self.symbol_name = parse_string_data(header_names.decode('utf-8'), self.st_name)
        else:
            self.symbol_name = None

    def __str__(self):
        return f"{self.symbol_name} @ 0x{self.st_value:0x}: {self.st_info}"

    def _parse_header(self, data, symbol_number):
        header = parse_header(data, symbol_number, self.sh_entsize, self.sh_offset, self.hdr_struct)
        return (
            header[0],  # st_name
            SymbolInfo(header[1]),  # st_info
            header[2] & 0x3,  # st_other
            header[3],  # st_shndx
            header[4],  # st_value
            header[5],  # st_size
        )

    def _repack_header(self):
        offset = self.sh_offset + (self.symbol_number * self.sh_entsize)
        repack_header(self._full_data, offset, self.sh_entsize, self.header, self.hdr_struct)


class SymbolInfo:
    def __init__(self, st_info):
        self.st_bind = SymbolBinding(st_info >> 4)
        self.st_type = SymbolType(st_info & 0xF)

    def get_value(self):
        return int(f"{self.st_bind.value:04b}{self.st_type.value:04b}", 2)

    def __str__(self):
        return f"[{self.st_type.name} @ {self.st_bind.name}]"


class Function:
    """ A helper class for returning details of specific functions within the binary """

    def __init__(self, name, offset, size):
        """
        Initialises a new Function given it's name, offset and size.
        :param name: The name of the function (from a symbol if available, otherwise randomly generated)
        :param offset: The address of the function in bytes within the binary
        :param size: The lenght of the function in bytes
        """
        self.name = name
        self.start_addr = offset
        self.size = size
        self.end_addr = offset + size

    def __str__(self):
        return f"{self.name} @ 0x{self.start_addr:x}"


if __name__ == '__main__':
    filename = '/home/james/dev/offside-trap/test/source/test'
    packed_filename = f"{filename}.packed"
    elf = ELF(filename)
    elf.append_loadable_segment_2(400)
    with open(packed_filename, 'wb') as f:
        f.write(elf.data)
