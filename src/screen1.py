from geant4_pybind import *
from geant4_pybind import G4Event, G4Step, G4TouchableHistory, G4VPhysicalVolume
import sys
import pandas as pd


# Создание геометрии

class ScreenGeometry(G4VUserDetectorConstruction):
    def __init__(self, a) -> None:
        super().__init__()
        self.a = a

    def Construct(self) -> G4VPhysicalVolume:
        nist = G4NistManager.Instance()
        checkOverlaps = True

        # World

        world_sizeXY = 30 * cm
        world_sizeZ = 30 * cm
        world_mat = nist.FindOrBuildMaterial('G4_AIR')
        # TODO: создать сплав из нескольких материалов
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

        # Материалы экранов
        screen_detXY = 250 * mm
        screen1_detZ = 1.5 * mm
        screen2_detZ = 0.5 * mm
        screen3_detZ = 1.5 * mm

        # Al
        mat1 = nist.FindOrBuildMaterial('G4_Al')
        # W
        mat2 = nist.FindOrBuildMaterial('G4_W')
        # Ni
        mat3 = nist.FindOrBuildMaterial('G4_Ni')

        # Положение экранов
        screen_coord = 5 * mm

        # Первый экран
        self.solid_screen1 = G4Box('Screen1', 0.5 * screen_detXY, 0.5 * screen_detXY, 0.5 * screen1_detZ)
        self.logic_screen1 = G4LogicalVolume(self.solid_screen1, mat1, "Screen1")
        self.phys_screen1 = G4PVPlacement(
            None,
            G4ThreeVector(0, 0, screen_coord),
            self.logic_screen1,
            "Screen1",
            logic_world,
            0,
            checkOverlaps
        )

        elem_name1 = 'Aluminium'
        elem_symbol1 = 'Al'
        elem_atomic_number1 = 13
        elem_density1 = 2.7
        elem_aem1 = 26.981
        elem_perc1 = 6
        #G4Element
        element1 = nist.FindOrBuildElement(elem_symbol1)

        elem_name2 = 'Titanium'
        elem_symbol2 = 'Ti'
        elem_atomic_number2 = 22
        elem_density2 = 4.54
        elem_aem2 = 47.867
        elem_perc2 = 94

        element2 = nist.FindOrBuildElement(elem_symbol2)
        
        avg_density = 4.43
        material = G4Material('ВТ5Л', avg_density * g/cm3, 2)
        material.AddElement(element1, 6*perCent)
        material.AddElement(element2, 94*perCent)

        with open('log.txt', 'w+') as f:
            f.write(element1.GetName() + '\n')
            f.write(str(element1.GetIndex()) + '\n')
            f.write(str(element1.GetAtomicMassAmu())+'\n')

            f.write(element2.GetName() + '\n')
            f.write(str(element2.GetIndex()) + '\n')
            f.write(str(element2.GetAtomicMassAmu())+'\n')

            f.write(material.GetName() + '\n')
            f.write(str(material.GetDensity()) + '\n')
            f.write(str(material))

        self.solid_screen2 = G4Box('Screen2', 0.5 * screen_detXY, 0.5 * screen_detXY, 0.5 * screen2_detZ)
        self.logic_screen2 = G4LogicalVolume(self.solid_screen2, material, "Screen2")
        self.phys_screen2 = G4PVPlacement(
            None,
            G4ThreeVector(0, 0, screen_coord + 0.5 * screen1_detZ + 0.5*screen2_detZ),
            self.logic_screen2,
            "Screen2",
            logic_world,
            0,
            checkOverlaps
        )

        

        
        

        return phys_world

    def ConstructSDandField(self) -> None:
        fSDM = G4SDManager.GetSDMpointer()

        # Объявляем экраны чувствительными
        screen1_detector_name = "Screen1_Detector"
        screen1_detector = ScreenDetector(screen1_detector_name)

        screen2_detector_name = "Screen2_Detector"
        screen2_detector = ScreenDetector(screen2_detector_name)

        # Добавляем их в менеджер детекторов
        fSDM.AddNewDetector(screen1_detector)
        self.logic_screen1.SetSensitiveDetector(screen1_detector)

        fSDM.AddNewDetector(screen2_detector)
        self.logic_screen2.SetSensitiveDetector(screen2_detector)

        

class ScreenDetector(G4VSensitiveDetector):

    def __init__(self, name):
        super().__init__(name)

    def ProcessHits(self, aStep: G4Step, hist: G4TouchableHistory) -> bool:
        edep = aStep.GetTotalEnergyDeposit()  # не понял че за энергия

        track = aStep.GetTrack()

        kinetic = aStep.GetTrack().GetKineticEnergy()

        if kinetic == 0 and track.GetDefinition().GetParticleName() == 'e-':
            print(f'{track.GetDefinition().GetParticleName()}\'s kinetic 0 in \
                  {track.GetVolume().GetName()} coord: {track.GetPosition().z / mm}')

        if edep == 0:
            return False

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
        # тут еще в примере что-то было


# Получение треков


class SteppingAction(G4UserSteppingAction):
    def __init__(self):
        super().__init__()
    steps = []

    def UserSteppingAction(self, step: G4Step, arr=steps) -> None:
        track = step.GetTrack()
        if track.GetCurrentStepNumber() == 1:
            parent_id, track_id, particle = (track.GetParentID(), track.GetTrackID(), \
                                             step.GetTrack().GetDefinition().GetParticleName())
            pos_X = step.GetPreStepPoint().GetPosition().x
            pos_Y = step.GetPreStepPoint().GetPosition().y
            pos_Z = step.GetPreStepPoint().GetPosition().z
            step_id = 0
            volume = track.GetVolume().GetName()
            material = track.GetMaterial().GetName()
            kinE = track.GetKineticEnergy()
            step_leng = track.GetStepLength()
            track_leng = track.GetTrackLength()
            step_info = {"parent_id": parent_id,
                         "track_id": track_id,
                         "particle": particle,
                         "Step": step_id,
                         "X": pos_X,
                         "Y": pos_Y,
                         "Z": pos_Z,
                         "kinE": kinE,
                         "volume": volume,
                         "material": material,
                         "step_len:": step_leng,
                         "track_len": track_leng}
            arr.append(step_info)
        parent_id, track_id, particle = (track.GetParentID(), track.GetTrackID(), \
                                         step.GetTrack().GetDefinition().GetParticleName())
        pos_X = track.GetPosition().x
        pos_Y = track.GetPosition().y
        pos_Z = track.GetPosition().z
        volume = track.GetVolume().GetName()
        material = track.GetMaterial().GetName()
        step_id = track.GetCurrentStepNumber()
        kinE = track.GetKineticEnergy()
        print(kinE)
        step_leng = track.GetStepLength()
        track_leng = track.GetTrackLength()
        step_info = {"parent_id": parent_id,
                     "track_id": track_id,
                     "particle": particle,
                     "Step": step_id,
                     "X": pos_X,
                     "Y": pos_Y,
                     "Z": pos_Z,
                     "kinE": kinE,
                     "volume": volume,
                     "material": material,
                     "step_len:": step_leng,
                     "track_len": track_leng}
        arr.append(step_info)




# Генерация частиц

class PrimaryGeneration(G4VUserPrimaryGeneratorAction):
    def __init__(self) -> None:
        super().__init__()

        n_particles = 1
        self.fParticleGun = G4ParticleGun(n_particles)

        particle_table = G4ParticleTable.GetParticleTable()
        particle = particle_table.FindAntiParticle("proton")

        self.fParticleGun.SetParticleDefinition(particle)
        self.fParticleGun.SetParticleMomentumDirection(G4ThreeVector(0., 0., 1.0))
        self.fParticleGun.SetParticleEnergy(50 * MeV)

    def GeneratePrimaries(self, anEvent: G4Event) -> None:

        worldLV = G4LogicalVolumeStore.GetInstance().GetVolume("World")
        world_box = None
        world_half_len = None

        if worldLV != None:
            world_box = worldLV.GetSolid()

        if world_box != None:
            world_half_len = world_box.GetZHalfLength()  # эту команду vs code не видит

        self.fParticleGun.SetParticlePosition(G4ThreeVector(0, 0, -5 * cm))
        self.fParticleGun.GeneratePrimaryVertex(anEvent)


class ActionInitialization(G4VUserActionInitialization):

    def Build(self) -> None:
        self.SetUserAction(SteppingAction())
        self.SetUserAction(PrimaryGeneration())


runManager: G4RunManager = G4RunManagerFactory.CreateRunManager(G4RunManagerType.Serial)

runManager.SetUserInitialization(ScreenGeometry(1))

physics = FTFP_BERT()
physics.SetVerboseLevel(1)

runManager.SetUserInitialization(physics)

runManager.SetUserInitialization(ActionInitialization())

runManager.Initialize()
# runManager.BeamOn(100)

ui = G4UIExecutive(len(sys.argv), sys.argv)
visManager = G4VisExecutive()
visManager.Initialize()

UImanager = G4UImanager.GetUIpointer()
UImanager.ApplyCommand('/control/execute init_vis.mac')
UImanager.ApplyCommand('/gun/particle proton')
UImanager.ApplyCommand('/gun/energy 40 MeV')
UImanager.ApplyCommand('/tracking/verbose 0')
UImanager.ApplyCommand('/run/beamOn 1')
df = pd.DataFrame(SteppingAction.steps)
# df.to_pickle("./screen.pkl")
print(df)
ui.SessionStart()
sys.exit()
