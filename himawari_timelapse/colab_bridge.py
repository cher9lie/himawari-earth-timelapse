"""Run the official CLI; Windows batch compatibility, no embedded credentials."""
import os
import sys

def main():
    if os.name=='nt':
        import types
        import builtins
        console=types.ModuleType('colab_cli.console')
        def unsupported(*args,**kwargs):
            raise RuntimeError('Windows 下请使用批处理命令；交互 console 请使用 WSL')
        console.connect_console=unsupported
        sys.modules['colab_cli.console']=console
        original=builtins.open
        class TTY:
            def __enter__(self):return sys.stdin
            def __exit__(self,*args):return False
        def portable(file,*args,**kwargs):
            if str(file)=='/dev/tty':return TTY()
            return original(file,*args,**kwargs)
        builtins.open=portable
    from colab_cli.cli import app
    app()

if __name__=='__main__':main()
