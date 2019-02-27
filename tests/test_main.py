
from hydra.main import HydraTest

def test_hydra(tmp):
    with HydraTest() as app:
        res = app.run()
        print(res)
        raise Exception

def test_command1(tmp):
    argv = ['command1']
    with HydraTest(argv=argv) as app:
        app.run()
