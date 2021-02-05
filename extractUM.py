import os, sys
import datetime as dt
import location_config as config
import std_functions as sf
import shutil
import pandas as pd
import iris
import pdb

'''
This script is a collection of functions to extract data from the Met Office archives and to share that data by FTP.
It should be able to perform two main tasks:
    1. Extract case study data for a given start and end datetimes, a given spatial subset and case study name, and upload to FTP
    2. Extract model data for the whole region for the last x days, share by FTP, and delete older data
'''

def post_process(start, end, bboxes, scratchfile, row, settings):
    '''
    Make a spatial subset of the files in filelist, and saves into the UM/CaseStudy or UM/RealTime folder
    :param start: datetime
    :param end: datetime
    :param filelist: list of full resolution files extracted from MASS
    :param bboxes: dictionary of list of floats or integers. Tells the function which regional and/or event domains to
                use. Domains are formatted either [xmin, ymin, xmax, ymax] or None
    :param row: pandas Series. A subset taken from sf.get_default_stash_proc_codes
    :param settings: ready from the .config file
    :return: list of files in the UM/CaseStudy directory to upload to FTP
    '''

    odir = settings['um_path'].rstrip('/') + '/' + settings['region_name'] + '/' + settings['location_name']

    ofile = sf.make_nice_filename(os.path.basename(scratchfile))
    init_dt = dt.datetime.strptime(ofile.split('_')[0], '%Y%m%dT%H%MZ')
    model_id = os.path.basename(scratchfile).split('_')[1]
    ofilepath = odir + '/' + init_dt.strftime('%Y%m/') + ofile

    if not os.path.isdir(os.path.dirname(ofilepath)):
        os.makedirs(os.path.dirname(ofilepath))

    # Loop through the dictionary of regions.
    # bboxes has 3 keys (tropics, region and event), which either contain a list of bbox coordinates or None
    # If the item contains coordinates, that means we want to subset it
    try:
        icube = iris.load_cube(scratchfile)
    except:
        print('Unable to load:', scratchfile)
        if os.path.isfile(scratchfile):
            os.remove(scratchfile)
        return

    ofilelist = []
    for k, val in bboxes.items():

        if val == 'tropics' and ('ga' in model_id or 'global' in model_id):
            # No need to process the global model at tropics scale (it's too big)
            continue

        if val:
            cube = icube.copy()
            ofile = ofilepath.replace('.nc', '_' + k + '.nc')

            if os.path.isfile(ofile):
                ofilelist.append(ofile)
            else:
                print('Saving:',ofile)
                if k == 'region':
                    if row.levels:
                        cube = cube.extract(iris.Constraint(pressure=[925., 850., 700., 500., 200.]))
                try:
                    cube = cube.intersection(latitude=(val[1], val[3]), longitude=(val[0], val[2]))
                    cube = sf.periodConstraint(cube, start, end, greedy=True)
                    iris.save(cube, ofile, zlib=True)
                    ofilelist.append(ofile)
                except TypeError:
                    continue
                except:
                    print('   File either outside bbox or time constraints')

    return ofilelist

def domain_size_decider(row, model_id, regbbox, eventbbox):
    '''
    Decides, using the stashdf row (from std_stashcodes.csv), whether we are subsetting using the bbox, the
    regional bounding box or the global tropics
    :param row: pandas Series. A subset taken from sf.get_default_stash_proc_codes
    :param model_id: string. The model identifier. Could be anyone of 'ga6', 'ga7', 'km4p4', 'indkm1p5', 'malkm1p5',
            'phikm1p5', 'global-prods' (global operational), 'africa-prods' (africa operational)
    :param regbbox: list of floats. Bounding box of the (larger) regional bbox. Contains [xmin, ymin, xmax, ymax]
    :param eventbbox: list of floats. Bounding box of the (smaller) event bbox. Contains [xmin, ymin, xmax, ymax]
    :return: dictionary of domains to use for subsetting. If a bbox is set to None, then it is not used for this
            stash code / region combination
    '''

    # Models that have a full global coverage
    global_models = ['analysis', 'global-prods', 'opfc']
    # Models that are likely to have a full regional coverage
    regional_models = ['analysis', 'ga6', 'ga7', 'km4p4', 'global', 'africa', 'opfc', 'psuite42']
    # No point sharing global data not in the tropics, and this reduces the data size by 2/3
    tropicsbbox = [-180, -30, 180, 30]

    try:
        tropics = tropicsbbox if row.share_tropics and model_id in global_models else None
        region = regbbox if row.share_region and model_id in regional_models else None
        event = eventbbox if row.share_event else None
    except:
        tropics = None
        region = None
        event = None

    return {'tropics': tropics, 'region': region, 'event': event}


def check_ofiles(init_times, stash, lbproc, bboxes, model_id, settings):
    # Does the output file exist?
    # If yes, add [init_time, stash, lbproc, region_name and location_name] to 'remove_from_scratch'
    # If no, and scratch file exists, add [init_time, stash, lbproc, region_name and location_name] to 'postprocess'
    # If no, and scratch file doesn't exist, add [init_time, stash, lbproc, region_name and location_name]
    #   to 'extract_to_scratch' AND 'postprocess'

    extract_to_scratch = []
    postprocess = []
    remove_from_scratch = []
    to_ftp = []

    stashdf = sf.get_default_stash_proc_codes()
    record = stashdf[(stashdf['stash'] == int(stash)) & (stashdf['lbproc'] == int(lbproc))]
    these_boxes = [k for k, v in bboxes.items() if v]

    for it in init_times:

        jobid = sf.getJobID_byDateTime(it, domain=sf.getModelDomain_bybox(settings['bbox']))
        if model_id == 'analysis':
            full_model_id = 'analysis'
        else:
            full_model_id = sf.getModelID_byDatetime(it, domain=sf.getModelDomain_bybox(settings['bbox']), searchtxt=model_id)['modellist'][0]
        scratchfile = settings['scratchdir'] + 'ModelData/' + \
                      jobid + '/' + it.strftime('%Y%m%dT%H%MZ') + '_' + \
                      full_model_id + '_' + str(stash) + '_' + str(lbproc) + '.nc'
        umdir = settings['um_path'].rstrip('/') + '/' + settings['region_name'] + '/' + settings['location_name'] + '/' + it.strftime('%Y%m')

        to_process = []
        for bk in these_boxes:
            file_nice = umdir + '/' + it.strftime('%Y%m%dT%H%MZ') + '_' + \
                        full_model_id.replace('_', '-') + '_' + \
                        record['name'].to_string(index=False).lstrip(' ') + '_' + \
                        sf.lut_lbproc(int(lbproc)) + '_' + \
                        bk + '.nc'

            if not os.path.isfile(file_nice):
                to_process.append(file_nice)
            else:
                to_ftp.append(file_nice)

        details = {'it': it, 'stash': stash, 'lbproc': lbproc, 'region_name': settings['region_name'], 'location_name': settings['location_name']}
        # Do the output files exist?
        if not to_process:
            # If yes, add [init_time, stash, lbproc, region_name and location_name] to 'remove_from_scratch'
            if os.path.isfile(scratchfile):
                remove_from_scratch.append(scratchfile)
        else:
            if os.path.isfile(scratchfile):
                # If no, and scratch file exists, add [init_time, stash, lbproc, region_name and location_name] to 'postprocess'
                postprocess.append(scratchfile)
                remove_from_scratch.append(scratchfile)
            else:
                # If no, and scratch file doesn't exist, add [init_time, stash, lbproc, region_name and location_name]
                #   to 'extract_to_scratch' AND 'postprocess'
                extract_to_scratch.append(details)
                postprocess.append(scratchfile)
                remove_from_scratch.append(scratchfile)

    return extract_to_scratch, postprocess, remove_from_scratch, to_ftp


def main(start=None, end=None, region_name=None, location_name=None, bbox=None, model_ids=None, ftp_upload=None):
    '''
    Loads data and runs all the precip plotting routines. The following variables are picked up from the settings dictionary
    :param start: datetime for the start of the case study
    :param end: datetime for the end of the case study
    :param region_name: String. Larger region E.g. 'SEAsia' or 'EastAfrica'
    :param location_name: String. Zoom area within the region. E.g. 'PeninsularMalaysia'
    :param bbox: List. Format [xmin, ymin, xmax, ymax]
    :param model_ids: list of model_ids
    :param ftp_upload: boolean
    :return lots of plots
    '''

    settings = config.load_location_settings()
    if not start:
        start = settings['start']
    if not end:
        end = settings['end']
    if not region_name:
        region_name = settings['region_name']
    if not location_name:
        location_name = settings['location_name']
    if not bbox:
        bbox = settings['bbox']
    if not model_ids:
        model_ids = settings['model_ids']
    if not ftp_upload:
        ftp_upload = settings['ftp_upload']

    # Gets all the stash codes tagged as share
    stashdf = sf.get_default_stash_proc_codes(list_type='long')

    # Gets the large scale bbox name (either 'SEAsia', 'Africa', or 'global')
    domain = sf.getModelDomain_bybox(bbox)
    regbbox = sf.getBBox_byRegionName(domain)
    if not model_ids:
        model_ids = sf.getModels_bybox(bbox)['model_list']

    # Set ftp path etc
    ftp_path = '/WCSSP/'+region_name+'/'+location_name+'/'
    remove_old = True
    # km1p5 data is too big to send in realtime
    model_ids = [mi for mi in model_ids if not 'km1p5' in mi]

    for row in stashdf.itertuples(index=False):

        # Get the UM analysis data
        # bboxes = domain_size_decider(row, 'analysis', regbbox, bbox)
        # ana_start = start.replace(hour=0, minute=0) - dt.timedelta(days=1)
        # ana_times = sf.make_timeseries(ana_start, end, 6)
        # # Checks whether files exist for this combination of region / location / init_time / model_id / stash / lbproc
        # extract_to_scratch, postprocess, remove_from_scratch, ftp_list = check_ofiles(ana_times, row.stash, row.lbproc,
        #                                                                               bboxes, 'analysis', settings)
        # for e2s in extract_to_scratch:
        #     filelist_analysis = sf.selectAnalysisDataFromMass(e2s['it'], e2s['it'], row.stash, lbproc=row.lbproc, lblev=row.levels)

        # for scratchfile in postprocess:
        #     if os.path.isfile(scratchfile):
        #         ofilelist = post_process(start, end, bboxes, scratchfile, row, settings)
        #         ftp_list.extend(ofilelist)
        #
        # for scratchfile in remove_from_scratch:
        #     if os.path.isfile(scratchfile):
        #         print('   Removing:', scratchfile)
        #         os.remove(scratchfile)
        #
        # if ftp_upload and ftp_list:
        #     sf.send_to_ftp(ftp_list, ftp_path, settings, removeold=remove_old)

        # Get the UM model data
        for model_id in model_ids:
            print(model_id, row.stash, row.lbproc)

            # For this model, get all the available start and end datetimes
            if model_id == 'analysis':
                ana_start = start.replace(hour=0, minute=0) - dt.timedelta(days=1)
                init_times = sf.make_timeseries(ana_start, end, 6)
            else:
                init_times = sf.getInitTimes(start, end, domain, model_id=model_id)

            # Decides whether we want tropics, region or event domains for this model_id / stash / lbproc combination
            bboxes = domain_size_decider(row, model_id, regbbox, bbox)

            # Checks whether files exist for this combination of region / location / init_time / model_id / stash / lbproc
            extract_to_scratch, postprocess, remove_from_scratch, ftp_list = check_ofiles(init_times, row.stash, row.lbproc, bboxes, model_id, settings)

            for e2s in extract_to_scratch:
                if model_id == 'analysis':
                    filelist_models = sf.selectAnalysisDataFromMass(e2s['it'], e2s['it'], row.stash,
                                                                      lbproc=row.lbproc, lblev=row.levels)
                else:
                    filelist_models = sf.selectModelDataFromMASS([e2s['it']], e2s['stash'], lbproc=e2s['lbproc'], lblev=row.levels,
                                                             domain=sf.getModelDomain_bybox(bbox), plotdomain=bbox,
                                                             modelid_searchtxt=model_id)

            for scratchfile in postprocess:
                if os.path.isfile(scratchfile):
                    ofilelist = post_process(start, end, bboxes, scratchfile, row, settings)
                    ftp_list.extend(ofilelist)

            for scratchfile in remove_from_scratch:
                if os.path.isfile(scratchfile):
                    print('   Removing:', scratchfile)
                    os.remove(scratchfile)

            if ftp_upload and ftp_list:
                sf.send_to_ftp(ftp_list, ftp_path, settings, removeold=remove_old)


if __name__ == '__main__':

    main()
