from geant4_pybind import *
from geant4_pybind import G4VPhysicalVolume
from DataServer import DataServer
from TrimParser import TrimParser
import sys

class ScreenGeometry(G4VUserDetectorConstruction):
    def __init__(self) -> None:
        super().__init__()
        self.ds = ds = DataServer()
        self.tp = TrimParser(data=ds.get_current_task_to_json(9))

    # TODO реализовать логику создания экранов/слоев из запроса
    def Construct(self) -> G4VPhysicalVolume:
        
        nist = G4NistManager.Instance()
        checkOverlaps = True

        # World

        world_sizeXY = 50 * cm
        world_sizeZ = 50 * cm
        world_mat = nist.FindOrBuildMaterial('G4_AIR')

        solid_world = G4Box("World", 0.5 * world_sizeXY, 0.5 * world_sizeXY, 0.5 * world_sizeZ)
        logic_world = G4LogicalVolume(solid_world, world_mat, "World")

        phys_world = G4PVPlacement(
            None,
            G4ThreeVector(),
            logic_world,
            "World",
            None,
            False,
            0,
            checkOverlaps
        )

        '''
            Возникает ошибка: Segmentation fault (core dumped)
        '''
        # Логика формирования экранов, по хорошему, конечно, стоит вынести это в отдельный метод
        materials = self.tp.readMaterials()
        element = nist.FindOrBuildElement(symb='Al', isotopes=False)
        print('=========================ELEMENT:===================')
        print(element)
        '''
        self.material_list = []
        for mat in materials:
            mat_name = mat.get('Name')
            mat_width = (float(mat.get('Width')) / 1000) * mm
            
            elements = mat.get('Elements')
            element_list = []
            
            for elem in elements:
                elem_name = elem.get('Name')
                elem_symbol = elem.get('Symbol')
                elem_atomic_number = float(elem.get('Atomic_number'))
                elem_density = float(elem.get('Density'))
                elem_aem = float(elem.get('Standard_atomic_weight'))
                elem_perc = float(elem.get('Percentage'))
                #мб попробовать другое название вбивать?
                #element = nist.FindOrBuildElement(elem_atomic_number, False)
                element = G4Element(elem_name, elem_symbol, elem_atomic_number, elem_aem*g/mole)
                #print(f'elem: {element}')
                element_list.append([element, elem_perc, elem_density])
            
            # Высчитывание процента
            total_perc = 0
            # Общий относительный процент
            for _ in element_list:
                total_perc += _[1]
            
            avg_density = 0
            for _ in element_list:
                # elem_perc = elem_perc / total
                _[1] = _[1] / total_perc
                # avg_density = elem_perc1 * elem_density1 + elem_perc2 * elem_density2
                avg_density += _[1] * _[2]
            
            components = len(element_list)
            print(components)
            print('creating material')
            material = G4Material(mat_name, avg_density*g/cm3, components)
            for _ in element_list:
                print(_[1])
                print(_[0].GetName())
                material.AddElement(_[0], frac=_[1])
            print('finished')
            print(material.GetName())
            self.material_list.append(material)

        print('mat finished')
        
        print('MATERIAL TABLE:')
        print(G4Material.GetMaterialTable())
        print('MATERIALS')
        '''
        return phys_world
    

class PrimaryGeneration(G4VUserPrimaryGeneratorAction):
    def __init__(self) -> None:
        super().__init__()

        n_particles = 1
        self.fParticleGun = G4ParticleGun(n_particles)

        particle_table = G4ParticleTable.GetParticleTable()
        particle = particle_table.FindAntiParticle("proton")

        self.fParticleGun.SetParticleDefinition(particle)
        self.fParticleGun.SetParticleMomentumDirection(G4ThreeVector(0., 0., 1.0))
        self.fParticleGun.SetParticleEnergy(4 * MeV)

    def GeneratePrimaries(self, anEvent: G4Event) -> None:

        worldLV = G4LogicalVolumeStore.GetInstance().GetVolume("World")
        print('world')
        world_box = None
        world_half_len = None

        if worldLV != None:
            world_box = worldLV.GetSolid()

        if world_box != None:
            world_half_len = world_box.GetZHalfLength()  # эту команду vs code не видит

        self.fParticleGun.SetParticlePosition(G4ThreeVector(0, 0, 5*mm ))
        self.fParticleGun.GeneratePrimaryVertex(anEvent)

class ActionInitialization(G4VUserActionInitialization):

    def Build(self) -> None:
        #self.SetUserAction(SteppingAction())
        self.SetUserAction(PrimaryGeneration())

if __name__ == '__main__':
    # Создаем объект класса, который будет общаться с серваком
    #ds = DataServer()
    #tp = TrimParser(data=ds.get_current_task_to_json(9))
    
    runManager: G4RunManager = G4RunManagerFactory.CreateRunManager(G4RunManagerType.Serial)
    
    runManager.SetUserInitialization(ScreenGeometry())
    
    physics = FTFP_BERT()
    physics.SetVerboseLevel(1)
    runManager.SetUserInitialization(physics)

    runManager.SetUserInitialization(ActionInitialization())

    runManager.Initialize()
    #print(G4LogicalVolumeStore.GetInstance().GetVolume('Screen-0--ВТ5Л'))
    runManager.BeamOn(1)

    #print(G4LogicalVolumeStore.GetInstance().GetVolume('Screen-'))


