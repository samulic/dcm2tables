import pydicom
import os
import json
import platform
from collections import OrderedDict
import subprocess


class DCMQINotFoundError(Exception):
  pass

class TIDNotSupportedError(Exception):
  pass

# recipe from
# https://stackoverflow.com/questions/377017/test-if-executable-exists-in-python/377028#377028
def which(program):
  import os
  def is_exe(fpath):
      return os.path.isfile(fpath) and os.access(fpath, os.X_OK)

  fpath, fname = os.path.split(program)
  if fpath:
    if is_exe(program):
      return program
  else:
    for path in os.environ["PATH"].split(os.pathsep):
      exe_file = os.path.join(path, program)
      if is_exe(exe_file):
        return exe_file
  return None


class DICOMParser(object):

  def __init__(self,fileName,rulesDictionary=None,tempPath=None, dcmqiPath=None, logger=None):
    try:
      self.dcm = pydicom.read_file(fileName)
    except:
      self.logger.debug ('Failed to read DICOM file using pydicom: '+fileName)
      raise

    self.fileName = fileName
    self.rulesDictionary = rulesDictionary
    self.tempPath = tempPath
    self.dcmqiPath = dcmqiPath

    self.tables = dict()

    self.tables["Instance2File"] = OrderedDict()
    self.tables["Instance2File"]["SOPInstanceUID"] = self.dcm.SOPInstanceUID
    self.tables["Instance2File"]["FileName"] = fileName

    if not logger:
      self.logger = logging.getLogger("dcm2tables.DICOMParser")
    else:
      self.logger = logger

  def getTables(self):
    return self.tables

  def parse(self):
    self.readTopLevelAttributes("CompositeContext")
    self.readReferences()

    modality = self.dcm.Modality

    if modality in ["MR", "SR", "PT", "CT", "SEG", "RWV"]:
      self.readTopLevelAttributes(self.dcm.Modality)

    if modality == "SEG":
      self.readSegments()
      self.readSegmentFrames()

    if modality == "SR":
      tid = self.dcm.ContentTemplateSequence[0].TemplateIdentifier
      if tid == "1500":
        outputJSON = os.path.join(self.tempPath, "measurements.json")
        tid1500reader = self.getTID1500readerExecutable()
        converterCmd = [tid1500reader, "--inputDICOM", self.fileName, "--outputMetadata", outputJSON]
        sp = subprocess.Popen(converterCmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (stdout,stderr) = sp.communicate()
        with open(outputJSON) as jsonFile:
          measurementsJSON = json.load(jsonFile)
          self.readMeasurements(measurementsJSON)
      else:
        raise TIDNotSupportedError("DICOM SR TID %s is currently not supported." % tid)

  def getTID1500readerExecutable(self):
    tid1500ReaderPath = 'tid1500reader'
    if platform.system() == 'Windows':
      tid1500ReaderPath += ".exe"
    if self.dcmqiPath:
      tid1500ReaderPath = os.path.join(self.dcmqiPath,tid1500ReaderPath)
    tid1500ReaderPath = which(tid1500ReaderPath)
    if not tid1500ReaderPath:
      raise DCMQINotFoundError('Could not find dcmqi executable tid1500reader.')
    return tid1500ReaderPath

  def readTopLevelAttributes(self,modality):
    self.tables[modality] = {}
    unresolvedAttributes = []
    for a in self.rulesDictionary[modality]:
      try:
        dataElement = self.dcm.data_element(a)
        if dataElement.VM>1:
          self.tables[modality][a] = '/'.join([str(i) for i in dataElement.value])
        else:
          self.tables[modality][a] = str(dataElement.value)
#        else:
#          self.tables[table]
#        self.tables[tableName][a] = self.dcm.data_element(a).value
#        print self.dcm.data_element(a).VM
      except:
        unresolvedAttributes.append(a)
        self.tables[modality][a] = None

    for a in unresolvedAttributes:
      if hasattr(self,"read"+modality+a):
        resolvedAttribute = str(getattr(self, "read%s%s" % (modality, a) )())
        self.tables[modality][a] = resolvedAttribute
        '''
        if resolvedAttribute is not None:
          print "Successfully resolved",a
        else:
          print "Failed to resolve",a
        '''

      #print self.tables[tableName][a]

  # functions to read specific attributes that are not top-level or that are SR #   tree elements
  #def readReferencedImageRealWorldValueMappingSequence(self):
  #  de = self.dcm.data_element("ReferencedImageRealWorldValueMappingSequence")
  #  if de:
  #    de = de.data_element("RealWorldValueMappingSequence")
  #    if de:

  # given the input data element, which must be a SQ, and must have the structure
  #  of items that follow the pattern ConceptNameCodeSequence/ConceptCodeSequence, find the sequence item that has
  #  ConceptNameCodeSequence > CodeMeaning, and return the data element corresponding
  #  to the ConceptCodeSequence matching the requested ConceptNameCodeSequence meaning
  def getConceptCodeByConceptNameMeaning(self,dataElement,conceptNameMeaning):
    for item in dataElement:
      if item.ConceptNameCodeSequence[0].CodeMeaning == conceptNameMeaning:
        return item.ConceptCodeSequence[0]

  def getMeasurementUnitsCodeSequence(self):
    dataElement = self.dcm.data_element("ReferencedImageRealWorldValueMappingSequence").value[0]
    dataElement = dataElement.data_element("RealWorldValueMappingSequence").value[0]
    dataElement = dataElement.data_element("MeasurementUnitsCodeSequence").value
    return dataElement

  def getQuantityDefinitionSequence(self):
    dataElement = self.dcm.data_element("ReferencedImageRealWorldValueMappingSequence").value[0]
    dataElement = dataElement.data_element("RealWorldValueMappingSequence").value[0]
    dataElement = dataElement.data_element("QuantityDefinitionSequence").value
    return dataElement

  def readRWVUnits_CodeValue(self):
    dataElement = self.getMeasurementUnitsCodeSequence()[0]
    return dataElement.CodeValue

  def readRWVUnits_CodingSchemeDesignator(self):
    dataElement = self.getMeasurementUnitsCodeSequence()[0]
    return dataElement.CodingSchemeDesignator

  def readRWVUnits_CodeMeaning(self):
    dataElement = self.getMeasurementUnitsCodeSequence()[0]
    return dataElement.CodeMeaning

  def readRWVQuantity_CodeValue(self):
    dataElement = self.getQuantityDefinitionSequence()
    dataElement = self.getConceptCodeByConceptNameMeaning(dataElement, "Quantity")
    return dataElement.CodeValue

  def readRWVQuantity_CodingSchemeDesignator(self):
    dataElement = self.getQuantityDefinitionSequence()
    dataElement = self.getConceptCodeByConceptNameMeaning(dataElement, "Quantity")
    return dataElement.CodingSchemeDesignator

  def readRWVQuantity_CodeMeaning(self):
    dataElement = self.getQuantityDefinitionSequence()
    dataElement = self.getConceptCodeByConceptNameMeaning(dataElement, "Quantity")
    return dataElement.CodeMeaning

  def readRWVMeasurementMethod_CodeValue(self):
    dataElement = self.getQuantityDefinitionSequence()
    dataElement = self.getConceptCodeByConceptNameMeaning(dataElement, "Measurement Method")
    return dataElement.CodeValue

  def readRWVMeasurementMethod_CodingSchemeDesignator(self):
    dataElement = self.getQuantityDefinitionSequence()
    dataElement = self.getConceptCodeByConceptNameMeaning(dataElement, "Measurement Method")
    return dataElement.CodingSchemeDesignator

  def readRWVMeasurementMethod_CodeMeaning(self):
    dataElement = self.getQuantityDefinitionSequence()
    dataElement = self.getConceptCodeByConceptNameMeaning(dataElement, "Measurement Method")
    return dataElement.CodeMeaning

  def readRWVRealWorldValueIntercept(self):
    dataElement = self.dcm.data_element("ReferencedImageRealWorldValueMappingSequence").value[0]
    dataElement = dataElement.data_element("RealWorldValueMappingSequence").value[0]
    return dataElement.RealWorldValueIntercept

  def readRWVRealWorldValueSlope(self):
    dataElement = self.dcm.data_element("ReferencedImageRealWorldValueMappingSequence").value[0]
    dataElement = dataElement.data_element("RealWorldValueMappingSequence").value[0]
    return dataElement.RealWorldValueSlope

  # this part is not driven at all by the QDBD schema!
  #  (maybe it should be changed to generalize things better)
  def readReferences(self):
    self.tables["References"] = []
    try:
      refSeriesSeq = self.dcm.data_element("ReferencedSeriesSequence")
    except KeyError:
      refSeriesSeq = None
    try:
      evidenceSeq = self.dcm.data_element("CurrentRequestedProcedureEvidenceSequence")
    except KeyError:
      evidenceSeq = None

    if refSeriesSeq:
      self.readReferencedSeriesSequence(refSeriesSeq)
    if evidenceSeq:
      self.readEvidenceSequence(evidenceSeq)

  def readPersonObserverName(self):
      item = self.findItemByConceptNameInContentSequence(self.dcm.ContentSequence, "Person Observer Name")
      return item.PersonName

  def readDeviceObserverName(self):
      item = self.findItemByConceptNameInContentSequence(self.dcm.ContentSequence, "Device Observer Name")
      return item.TextValue

  def readObserverType(self):
      item = self.findItemByConceptNameInContentSequence(self.dcm.ContentSequence, "Observer Type")
      return item.ConceptCodeSequence[0].CodeMeaning

  def readReferencedSeriesSequence(self, seq):
    for r in seq:
      seriesUID = r.data_element("SeriesInstanceUID").value
      try:
        refInstancesSeq = r.data_element("ReferencedInstanceSequence").value
        for item in refInstancesSeq:
          self.readReference(item, seriesUID)
      except KeyError as exc:
        self.logger.error ("Missing key: %s " % exc)

  def readEvidenceSequence(self, seq):
    for l1item in seq:
      try:
        seriesSeq = l1item.data_element("ReferencedSeriesSequence").value
        for l2item in seriesSeq:
          sopSeq = l2item.data_element("ReferencedSOPSequence").value
          seriesUID = l2item.SeriesInstanceUID
          for item in sopSeq:
            self.readReference(item, seriesUID)
      except KeyError as exc:
        self.logger.error ("Missing key: %s " % exc)

  def readReference(self, item, seriesUID):
    try:
      refClassUID = item.ReferencedSOPClassUID
      refInstanceUID = item.ReferencedSOPInstanceUID
      self.tables["References"].append({
        "SOPInstanceUID": self.dcm.SOPInstanceUID, "ReferencedSOPClassUID": refClassUID,
        "ReferencedSOPInstanceUID": refInstanceUID, "ReferencedSeriesInstanceUID": seriesUID
      })
    except KeyError as exc:
      self.logger.error ("Missing key: %s " % exc)

  def readSegments(self):
    seq = self.dcm.data_element("SegmentSequence")
    self.tables["SEG_Segments"] = []

    for segment in seq:
      sAttr = {}

      # Attribute should be either in a sub-sequence, at the
      #  top level of the sequence, or at the top level of the dataset
      #  Try all those options
      for attr in self.rulesDictionary["SEG_Segments"]:
        if attr.find("_")>0:
          # it is (supposed to be!) a code tuple in a sequence
          seqName,attrName = attr.split("_")
          try:
            sAttr[attr] = segment.data_element(seqName)[0].data_element(attrName).value
          except:
            sAttr[attr] = None
        else:
          try:
            sAttr[attr] = segment.data_element(attr).value
          except Exception as e:
            try:
              sAttr[attr] = self.dcm.data_element(attr).value
            except:
              sAttr[attr] = None

      self.tables["SEG_Segments"].append(sAttr)

  def readSegmentFrames(self):
    pfFG = self.dcm.data_element("PerFrameFunctionalGroupsSequence")
    sFG = self.dcm.data_element("SharedFunctionalGroupsSequence")

    self.tables["SEG_SegmentFrames"] = []

    # Attribute should be either in a sub-sequence, in the shared FG,
    #  or at the top level of the dataset
    #  Try all those options
    for frame in pfFG:
      fAttr = {}
      for attr in self.rulesDictionary["SEG_SegmentFrames"]:
        # recursively search in the per-frame FG item
        value = self.recursiveFindInDataset(frame,attr)
        if value is None:
          # recursively search in the shared FG
          value = self.recursiveFindInDataElement(sFG,attr)
        # if those fail, look it up top-level
        if value is None:
          value = self.dcm.data_element(attr).value
        fAttr[attr] = value
      self.tables["SEG_SegmentFrames"].append(fAttr)

  # idea from https://github.com/pieper/Chronicle/blob/master/bin/record.py#L58
  def recursiveFindInDataElement(self,de,attr):
    if de.keyword == attr:
      return de.value
    elif de.VR == "SQ":
      for item in de:
        return self.recursiveFindInDataset(item,attr)
    return None

  def recursiveFindInDataset(self,ds,attr):
    for key in ds.keys():
      de = ds[key]
      value = self.recursiveFindInDataElement(de,attr)
      if value is not None:
        return value
    return None

  def findItemByConceptNameInContentSequence(self,seq,conceptName):
    for item in seq:
      if type(item) == "pydicom.sequence.Sequence":
        self.findByConceptNameInContentSequence(item,conceptName)
      elif item.ConceptNameCodeSequence[0].CodeMeaning == conceptName:
        return item
    return None

  def readMeasurements(self,measurements):
    self.tables["SR1500_MeasurementGroups"] = []
    self.tables["SR1500_Measurements"] = []
    self.tables["SR1500_QualitativeEvaluations"] = []

    for mg in measurements["Measurements"]:
      mAttr = {}
      for attr in self.rulesDictionary["SR1500_MeasurementGroups"]:
        value = ''
        # first try to find it in the top-level of the measurements group json
        if attr in mg.keys():
          try:
            value = mg[attr]
          except:
            pass
        elif attr.find("_")>0:
          # this is a code sequence
          concept = attr.split("_")[0]
          item = attr.split("_")[1]
          try:
            value = mg[concept][item]
          except:
            pass
        elif hasattr(self, "read"+attr):
          try:
            value = str(getattr(self, "read%s" % attr )())
          except:
            pass
        else:
          # if all other attempts fail, read it at the top level of the
          #   DICOM dataset (it must be a foreign key)
          try:
            value = self.dcm.data_element(attr).value
          except:
            self.logger.error("Failed to look up \""+attr+"\"")
        mAttr[attr] = value

      self.tables["SR1500_MeasurementGroups"].append(mAttr)

      for mi in mg["measurementItems"]:
        miAttr = {}
        # OMG! Such a terrible code duplication
        for iattr in self.rulesDictionary["SR1500_Measurements"]:
          # first try to find it in the top-level of the measurements group json
          if iattr in mi.keys():
            value = mi[iattr]
          elif iattr.find("_")>0:
            # this is a code sequence
            concept = iattr.split("_")[0]
            item = iattr.split("_")[1]
            try:
              value = mi[concept][item]
            except:
              value = None
          # the attribute is one level above!
          #  our secondary foreign key is TrackingUniqueIdentifier ...
          # So this is a tiny bit different from the code above!
          elif iattr in mAttr.keys():
            value = mAttr[iattr]
          elif hasattr(self, "read"+iattr):
            value = str(getattr(self, "read%s" % iattr )())
          else:
            # if all other attempts fail, read it at the top level of the
            #   DICOM dataset (it must be a foreign key)
            value = self.dcm.data_element(iattr).value
          miAttr[iattr] = value
        self.tables["SR1500_Measurements"].append(miAttr)

      for qi in mg["qualitativeEvaluations"]:
        # duplicated from the above
        qiAttr = {}
        for iattr in self.rulesDictionary["SR1500_QualitativeEvaluations"]:
          # first try to find it in the top-level of the measurements group json
          if iattr in qi.keys():
            value = qi[iattr]
          elif iattr.find("_")>0:
            # this is a code sequence
            concept = iattr.split("_")[0]
            item = iattr.split("_")[1]
            try:
              value = qi[concept][item]
            except:
              value = None
          elif iattr in mAttr.keys():
            value = mAttr[iattr]
          elif hasattr(self, "read"+iattr):
            value = str(getattr(self, "read%s" % iattr )())
          else:
            # if all other attempts fail, read it at the top level of the
            #   DICOM dataset (it must be a foreign key)
            value = self.dcm.data_element(iattr).value
          qiAttr[iattr] = value
        self.tables["SR1500_QualitativeEvaluations"].append(qiAttr)
