import sys
from argparse import ArgumentParser
from random import Random

from elf_packer import ELFPacker


def check_args(parser, arg_list):
    """ Checks the command line arguments sent by the user are valid.

    :param parser: The ArgumentParser
    :param arg_list: The collection of arguments
    """
    # One of list or encrypt needs to be chosen
    if (arg_list.encrypt is False and
            arg_list.list is False):
        parser.error("Either the --encrypt or --list flag must be selected")

    # Encryption needs a key
    if (arg_list.encrypt is True and
            arg_list.key is None and
            arg_list.random is False):
        parser.error("Either a key or the random flag needs to be supplied when encrypting a binary.")

    # Encryption needs either random or supplied key, but not both
    if (arg_list.encrypt is True and
            arg_list.key is not None and
            arg_list.random is True):
        parser.error("Only one of either an encryption key or random may be selected.")

    # Either a list of functions or all functions must be selected
    if (arg_list.encrypt is True and
            arg_list.function is None and
            arg_list.all is False):
        parser.error("A list of functions with the --function flag, or "
                     "the --all flag needs to be supplied when encrypting.")


def parse_arguments():
    """ Parses the arguments given to the application by the user

    :return: A collection of parsed arguments
    """
    parser = ArgumentParser(description='Encrypt a binary')

    # Required
    parser.add_argument('binary', metavar='BINARY', help='The binary to encrypt')

    # Listing functions
    parser.add_argument('-l', '--list', action='store_true', help='List the functions available to encrypt')

    # Encrypting the binary
    parser.add_argument('-e', '--encrypt', action='store_true', help="Encrypt the binary")
    parser.add_argument('-k', '--key', help='The RC4 key used to encrypt the binary')
    parser.add_argument('-r', '--random', action='store_true', help='Choose a random key to encrypt with')
    parser.add_argument('-f', '--function', action='append', help='A function to encrypt (list multiple if required)')
    parser.add_argument('-a', '--all', action='store_true', help='Encrypt all functions')

    arglist = parser.parse_args()

    # Ensure args are valid
    check_args(parser, arglist)

    return arglist


if __name__ == '__main__':
    args = parse_arguments()

    # Parse the ELF binary
    packer = ELFPacker(args.binary)

    # If list is chosen, simply list the available functions and exit
    if args.list:
        for symbol in packer.list_functions():
            print(symbol)
        exit(0)

    # Otherwise encrypt the binary with a specified or random key
    if args.encrypt:
        rnd = Random()
        key = args.key if args.key is not None else rnd.randint(0, 100)
        all_functions = packer.list_functions()
        functions = [f for f in all_functions if f.name in args.function or args.all]
        if len(functions) != len(args.function):
            print(f"Not all functions were found within the binary. Try again with the -l flag.", file=sys.stderr)
            exit(-1)
        packer.encrypt(key, functions)
