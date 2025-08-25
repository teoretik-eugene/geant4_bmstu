#from geant4_pybind import * # TODO сделать подгрузку только нескольких методов

class TrimParser:

    def __init__(self, data: dict) -> None:
        self.data = data
    
    def readParticleText(self) -> dict:
        data_ls = self.data.split('\n')
        dict_particle = {}
        dict_particle[str(data_ls[1]).replace('\r', '')] = list(map(lambda x: float(x), data_ls[2:7]))

        pass

    # Метод считывания материалов, необходимых для построения экранов
    def readMaterials(self) -> dict:
        return self.data.get('Screen').get('Materials')
    
    def sumWidth(self):
        width_list = self.data.get('Screen').get('Materials')
        sum_width = 0
        return sum(list(map(lambda x: float(x.get("Width"))/1000.0, width_list)))

