from geant4_pybind import *
from geant4_pybind import G4Event, G4Step, G4TouchableHistory, G4VPhysicalVolume
from DataServer import DataServer
from TrimParser import TrimParser
import sys
import pyvista
import numpy as np


class ScreenGeometry(G4VUserDetectorConstruction):
    # Убрали параметр taskId
    def __init__(self, data, screen_info: dict) -> None:
        super().__init__()
        # self.ds = DataServer()
        # self.tp = TrimParser(data=self.ds.get_current_task_to_json(task_id))
        self.tp = TrimParser(data)
        self.screen_info = screen_info

        # Сделаю тут вывод некоторых данных  в файл
        with open('out.txt', 'w+') as f:
            f.write('\t\tOutput data: \n')

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
        # Чисто для проверки
        self.screen_info['Materials'] = []
        material_list = []
        indx = 0
        for mat in materials:
            mat_name = mat.get('Name')
            mat_width = (float(mat.get('Width')) / 1000) * mm

            self.screen_info.get('Materials').append({'Name': mat_name})
            self.screen_info.get('Materials')[indx]['Primary_stuck_count'] = 0
            self.screen_info.get('Materials')[indx]['Secondary_stuck_count'] = 0
            indx += 1

            elements = mat.get('Elements')
            element_list = []

            for elem in elements:
                elem_name = elem.get('Name')
                elem_symbol = elem.get('Symbol')
                elem_atomic_number = float(elem.get('Atomic_number'))
                elem_density = float(elem.get('Density'))
                elem_aem = float(elem.get('Standard_atomic_weight'))
                elem_perc = float(elem.get('Percentage'))
                # мб попробовать другое название вбивать?
                # element = nist.FindOrBuildElement(elem_atomic_number, False)
                element = nist.FindOrBuildElement(elem_symbol)
                # print(f'elem: {element}')
                element_list.append([element, elem_perc, elem_density])

            # Высчитывание процента
            total_perc = 0
            # Общий относительный процент
            for _ in element_list:
                total_perc += _[1]

            avg_density = 0
            for _ in element_list:
                # Описание действия: elem_perc = elem_perc / total
                _[1] = _[1] / total_perc
                # Описание действия: avg_density = elem_perc1 * elem_density1 + elem_perc2 * elem_density2
                avg_density += _[1] * _[2]

            # Создаем материал
            components = len(element_list)
            material = G4Material(mat_name, avg_density * g / cm3, components)
            for _ in element_list:
                material.AddElement(_[0], frac=_[1])
            print(material.GetName())
            material_list.append([material, mat_width, mat_name])

        screen_detXY = 250 * mm
        screen_coord = 15 * mm
        global s_info
        s_info = []
        # Создаем экраны и помещаем в лист логические объемы, чтобы потом их использовать в SD
        self.screen_list = []
        num = 0
        for mat in material_list:
            screen_name = f'Screen-- {num}'
            screen_width = mat[1]
            screen_material = mat[0]
            num += 1
            solid_screen = G4Box(screen_name, 0.5 * screen_detXY, 0.5 * screen_detXY, 0.5 * screen_width)
            logic_screen = G4LogicalVolume(solid_screen, screen_material, screen_name)
            phys_screen = G4PVPlacement(
                None,
                G4ThreeVector(0, 0, screen_coord + 0.5 * screen_width),
                logic_screen,
                screen_name,
                logic_world,
                0,
                checkOverlaps
            )
            # s_info.append([screen_detXY, screen_width, screen_coord])
            s_info.append([[-0.5*screen_detXY, -0.5 * screen_detXY, screen_coord],
                           [0.5*screen_detXY, -0.5 * screen_detXY, screen_coord],
                           [0.5*screen_detXY, 0.5 * screen_detXY, screen_coord],
                           [-0.5*screen_detXY, 0.5 * screen_detXY, screen_coord],
                           [-0.5*screen_detXY, -0.5 * screen_detXY, screen_coord]])
            screen_coord += screen_width
            self.screen_list.append(logic_screen)
            # Создаем лист длиной как лист экранов
            # В этом листе будет храниться количество застрявших частиц в слое экрана
            self.particles_count = [0] * len(self.screen_list)

        return phys_world

    def ConstructSDandField(self) -> None:
        fSDM = G4SDManager.GetSDMpointer()

        screen_ls = self.screen_list

        num = 0
        for sc in screen_ls:
            screen_detector_name = f'Screen_Detector - {num}'
            screen_detector = ScreenSensitiveDetector(screen_detector_name, screen_info)
            fSDM.AddNewDetector(screen_detector)
            sc.SetSensitiveDetector(screen_detector)


class ScreenEventAction(G4UserEventAction):

    def __init__(self) -> None:
        super().__init__()
        self.eventId = None

    def BeginOfEventAction(self, anEvent: G4Event) -> None:
        self.eventId = anEvent.GetEventID()

    def EndOfEventAction(self, anEvent: G4Event) -> None:
        self.eventId = None


class ScreenSteppingAction(G4UserSteppingAction):
    def __init__(self, primaryParticlesOutId: list, secondaryParticlesOutId: list, screen_end,
                 eventAction: ScreenEventAction) -> None:
        super().__init__()
        self.screen_end_coord = screen_end
        self.eventAction = eventAction

    global df
    df = {}
    def UserSteppingAction(self, step: G4Step) -> None:
        track = step.GetTrack()
        pos_x = step.GetPostStepPoint().GetPosition().x
        pos_y = step.GetPostStepPoint().GetPosition().y
        pos_z = step.GetPostStepPoint().GetPosition().z

        eventTrack = (self.eventAction.eventId, track.GetTrackID())

        # Проверка вылетевших первичных частиц
        if pos_x > self.screen_end_coord and not (eventTrack in primaryParticlesOutId) \
                and track.GetTrackID() == 1:
            primaryParticlesOutId.append(eventTrack)

        # Проверка вылетевших вторичных частиц
        if pos_x > self.screen_end_coord and not (eventTrack in secondaryParticlesOutId) \
                and track.GetTrackID() > 1:
            secondaryParticlesOutId.append(eventTrack)

        # Создание json для визуализации
        if eventTrack not in df:
            df[eventTrack] = []
        df[eventTrack].append([pos_x, pos_y, pos_z])
        # df.append({
        #     "event_id": eventTrack[0],
        #     "track_id": eventTrack[1],
        #     "X": pos_x,
        #     "Y": pos_y,
        #     "Z": pos_z,
        # })


class ScreenSensitiveDetector(G4VSensitiveDetector):

    def __init__(self, name: str, screen_info: dict):
        super().__init__(name)
        self.screen_info = screen_info

    def ProcessHits(self, aStep: G4Step, hist: G4TouchableHistory) -> bool:
        edep = aStep.GetTotalEnergyDeposit()

        track = aStep.GetTrack()

        kinetic = aStep.GetTrack().GetKineticEnergy()

        # Если кинетическая энергия у первичной частицы 0, то записываем в файл (просто тест)
        # if(kinetic == 0 and track.GetTrackID() == 1 and track.GetDefinition().GetParticleName() == 'He3'):
        if (kinetic == 0 and track.GetTrackID() == 1):
            screen_num = int(track.GetVolume().GetName()[9::])
            self.screen_info.get('Materials')[screen_num]['Primary_stuck_count'] += 1
            with open('out.txt', 'a') as f:
                f.write(
                    f'{track.GetTrackID()}:\t{str(track.GetVolume().GetName())}\t{track.GetDefinition().GetParticleName()}\n')

        if (kinetic == 0 and track.GetTrackID() > 1):
            screen_num = int(track.GetVolume().GetName()[9::])
            self.screen_info.get('Materials')[screen_num]['Secondary_stuck_count'] += 1
            with open('out.txt', 'a') as f:
                f.write(
                    f'{track.GetTrackID()}:\t{str(track.GetVolume().GetName())}\t{track.GetDefinition().GetParticleName()}\n')

        newHit = TrackerHit(aStep.GetTrack().GetTrackID,
                            edep,
                            aStep.GetPostStepPoint().GetPosition(),
                            aStep.GetPostStepPoint().GetKineticEnergy())
        return True


class TrackerHit(G4VHit):

    def __init__(self, trackID, edep, pos, kinetic) -> None:
        super().__init__()
        self.fTrackID = trackID
        self.fEdep = edep
        self.fPos = pos
        self.fKinetic = kinetic

    def Draw(self) -> None:
        vVisManager = G4VVisManager.GetConcreteInstance()
        if vVisManager != None:
            circle = G4Circle(self.fPos)
            circle.SetScreenSize(4)
            circle.SetFillStyle(G4Circle.filled)
            colour = G4Colour(1, 0, 0)
            attribs = G4VisAttributes()
            attribs.SetColor(colour)
            circle.SetVisAttributes(attribs)
            vVisManager.Draw(circle)

    def Print(self) -> None:
        print(f"TrackID: {self.fTrackID}  =====  Edep: {self.fEdep}")


class PrimaryGeneration(G4VUserPrimaryGeneratorAction):
    def __init__(self) -> None:
        super().__init__()

        # Количество частиц в одном событии
        n_particles = 1
        self.fParticleGun = G4ParticleGun(n_particles)

        particle_table = G4ParticleTable.GetParticleTable()
        # particle = particle_table.FindAntiParticle("proton")
        global main_part
        particle = particle_table.FindParticle(main_part:='He3')

        self.fParticleGun.SetParticleDefinition(particle)
        self.fParticleGun.SetParticleMomentumDirection(G4ThreeVector(0., 0., 1.0))
        self.fParticleGun.SetParticleEnergy(40 * MeV)

    def GeneratePrimaries(self, anEvent: G4Event) -> None:

        worldLV = G4LogicalVolumeStore.GetInstance().GetVolume("World")
        world_box = None
        world_half_len = None

        if worldLV != None:
            world_box = worldLV.GetSolid()

        if world_box != None:
            world_half_len = world_box.GetZHalfLength()  # эту команду vs code не видит

        # Следует выбрать мб норм расстояние?
        self.fParticleGun.SetParticlePosition(G4ThreeVector(0, 0, -50 * cm))
        self.fParticleGun.GeneratePrimaryVertex(anEvent)


class ActionInitialization(G4VUserActionInitialization):

    def __init__(self, data: dict) -> None:
        super().__init__()
        tp = TrimParser(data)
        # Координата крайней поверхности экранов
        self.screen_cord = (15 + tp.sumWidth()) * mm

    def Build(self) -> None:
        # self.SetUserAction(SteppingAction())
        self.SetUserAction(PrimaryGeneration())
        eventAction = ScreenEventAction()
        self.SetUserAction(eventAction)
        self.SetUserAction(
            ScreenSteppingAction(primaryParticlesOutId, secondaryParticlesOutId, self.screen_cord, eventAction))


if __name__ == '__main__':
    # Создаем объект класса, который будет общаться с серваком
    ds = DataServer()
    # tp = TrimParser(data=ds.get_current_task_to_json(9))
    task_id = 9  # примеры тасков: 9, 103, 170
    data = ds.get_current_task_to_json(task_id)
    screen_info = {}  # словарь для записи информации о каждом экране

    primaryParticlesOutId = []
    secondaryParticlesOutId = []

    event_num = 3 # количество генерируемых событий
    total_particles = event_num  # общий подсчет частиц

    runManager: G4RunManager = G4RunManagerFactory.CreateRunManager(G4RunManagerType.Serial)

    runManager.SetUserInitialization(ScreenGeometry(data=data, screen_info=screen_info))

    physics = FTFP_BERT()
    physics.SetVerboseLevel(1)
    runManager.SetUserInitialization(physics)

    runManager.SetUserInitialization(ActionInitialization(data=data))

    runManager.Initialize()

    # Количество запускаемых событий и общее количество первичных запускаемых части
    runManager.BeamOn(event_num)

    screen_particles = {}
    screen_particles['Screen'] = screen_info
    screen_particles['Total_particles'] = total_particles
    # screen_particles['Total_out_particles'] = len(p)
    screen_particles['Total_out_primary_particles'] = len(primaryParticlesOutId)
    screen_particles['Total_out_secondary_particles'] = len(secondaryParticlesOutId)

    # Визуализация
    pl = pyvista.Plotter()
    for key in df:
        points = np.array(df[key])
        actor = pl.add_lines(points, color='purple', width=3, connected=True)
    for i in s_info:
        points = np.array(i)
        actor = pl.add_lines(points, color='blue', width=2, connected=True)
    pl.camera_position = 'xy'
    # pl.export_html(f'{task_id}_{event_num}_{main_part}.html')
    pl.show()

    # Сформировать ответ
    ds = DataServer()
    print(ds.to_json(screen_particles))
    # print(particlesOutId)

    '''
    ui = G4UIExecutive(len(sys.argv), sys.argv)
    visManager = G4VisExecutive()
    visManager.Initialize()

    UImanager = G4UImanager.GetUIpointer()
    UImanager.ApplyCommand('/control/execute init_vis.mac')
    UImanager.ApplyCommand('/gun/particle proton')
    UImanager.ApplyCommand('/gun/energy 40 MeV')
    UImanager.ApplyCommand('/tracking/verbose 1')
    UImanager.ApplyCommand('/run/beamOn 50')
    ui.SessionStart()
    sys.exit()
    '''
