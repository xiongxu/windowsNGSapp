#!C:\Python27\python.exe

"""Module Description

Copyright (c) 2009 H. Gene Shin <shin@jimmy.harvard.edu>

This code is free software; you can redistribute it and/or modify it
under the terms of the BSD License (see the file COPYING included with
the distribution).

@status:  experimental
@version: $Revision$
@author:  H. Gene Shin
@contact: shin@jimmy.harvard.edu
"""

# ------------------------------------
# python modules
# ------------------------------------
import os
import sys
import re
import logging
import operator
import itertools
import subprocess
import warnings
from optparse import OptionParser
import CEAS.inout as inout
import CEAS.R as R
import CEAS.annotator as annotator
import CEAS.sampler as sampler
import CEAS.profiler as profiler
import CEAS.tables as tables
import CEAS.corelib as corelib
#from CEAS.inout import MYSQL

# ------------------------------------
# constants
# ------------------------------------
logging.basicConfig(level=20,
                    format='%(levelname)-5s @ %(asctime)s: %(message)s ',
                    datefmt='%a, %d %b %Y %H:%M:%S',
                    stream=sys.stderr,
                    filemode="w"
                    )

# ------------------------------------
# Misc functions
# ------------------------------------
error   = logging.critical		# function alias
warn    = logging.warning
debug   = logging.debug
info    = logging.info

# ------------------------------------
# Main function
# ------------------------------------
def main():
    
    # read the options and validate them
    options=opt_validate(prepare_optparser())
    
    # print out the options
    info("\n" + options.argtxt)

    # read the gene annotation table
    jobcount=1
    info("#%d read the gene table..." %jobcount)
    GeneT = inout.GeneTable()
    try:
    	GeneT.read(Host = options.Host, User= options.User, Db=options.gdb, annotation='GeneTable', \
    	           columns=('name','chrom','strand','txStart','txEnd','cdsStart','cdsEnd','exonCount','exonStarts', 'exonEnds', 'name2'))
    except Exception, e:
        # if 'name2' does not exist and the user wants to use 'name2', error; otherwise, 
        if re.search(r'column.*name2', str(e)):
            if options.name2:
                error("The gene annotation table does not have 'name2.' Only gene IDs of 'name' can be used in wig profiling of the sub-groups of genes given though --gn-groups.")
                sys.exit(1)
            else:
                GeneT.read(Host = options.Host, User= options.User, Db=options.gdb, annotation='GeneTable', \
                           columns=('name','chrom','strand','txStart','txEnd','cdsStart','cdsEnd','exonCount','exonStarts', 'exonEnds')) 
        else:
            raise 
    
    GeneT.sort()
    chroms_GeneT=GeneT.get_chroms()
    chroms_GeneT=filter_chroms(chroms_GeneT,'_[A-Za-z0-9]*')
    
    # determine the metagenesize, concated exon size, concatenated intron size
    if options.metagene_size:
        options.catexon_size, options.catintron_size = corelib.find_nearest_multiple(options.metagene_size/2, options.pf_res), corelib.find_nearest_multiple(options.metagene_size/2, options.pf_res)
    else:    
        options.metagene_size = return_med_gene(GeneT, options.pf_res)
        options.catexon_size, options.catintron_size = return_med_catexon_catintron(GeneT, options.pf_res)
    
    # get the exon and intron sizes to consider in the average profiling
    options = determine_exon_intron_sizes(GeneT, options)    
    jobcount+=1

    if options.chipannot:
        # read ChIP regions
        info("#%d read the bed file of ChIP regions..." %jobcount)
        ChIP=inout.Bed()
        ChIP.read(options.bed)
        ChIP.sort()
        
        # test if the ChIP BED is valid or not.
        test_result = inout.test_if_valid_BED( ChIP )
        if test_result != None:
            error( 'ChIP BED file format is not valid! %s' %test_result )
            sys.exit(1)

        Csampler=sampler.ChIPSampler()
        ChIPSamp=Csampler.sample(ChIP,resolution=options.chip_res)
        jobcount+=1
    
        # do gene-centered annotation
        info('#%d perform gene-centered annotation...' %jobcount)
        GAnnotator=annotator.GeneAnnotator()
        GAnnotator.annotate(GeneT, ChIP, u=options.span, d=options.span)
        GAnnotator.write(options.name, description=True)
        jobcount+=1
        info('#%d See %s for gene-centered annotation!' %(jobcount, options.name+'.xls'))
        jobcount+=1
    
    # read regions of interest if it is given
    if options.chipannot and options.ebedannot:
        info("#%d read the bed file of regions of interest..." %jobcount)
        roi=inout.Bed()
        roi.read(options.ebed)
        jobcount+=1
    else: roi=None

    # if background annotation is not being run.
    if options.chipannot and not options.rebg:
        
        # iterate through chromosomes of the gene table
        info("#%d read the pre-computed genome bg annotation..." %jobcount)
        try:
            GenomeBGS=tables.SummaryGBG(name='GenomeBGS')
            GenomeBGS.readdb(Db=options.gdb)
            
            GenomePieP = tables.PieP( name='GenomePieP' )
            GenomePieP.readdb( Db=options.gdb )
        except:
            error("the pre-computed genome bg annotation is required for ChIP annotation. Use the gene annotation table files provided by CEAS or add a WIG file and set --bg.")
            sys.exit(1)
        GP=_interpoloate_gbg(options.gdb,options.sizes,options.bisizes)  # interpolation
        chroms_bg=GP.get_chroms()
        jobcount+=1
        
        # if any regions of interest are given
        if options.ebedannot:
            GP=_get_bgroi(GP,GenomeBGS,roi=roi,bg_res=options.bg_res)
        
        # annotate ChIP regions
        info('#%d perform ChIP region annotation...' %jobcount)
        Annot=annotator.Annotator()
        ChIPA=Annot.annotate(genome_coordinates=ChIPSamp,gene_table=GeneT,roi=roi,prom=options.sizes,biprom=options.bisizes,down=options.sizes,gene_div=(3,5) )

        CS,CP=Annot.summarize(ChIPA)
        CES, CEP = Annot.obtain_distribution_of_sites(ChIPA)
        
        # make the table complete with missing chromsomes, if there are
        annotator.make_table_complete(CS, chroms_bg)
        annotator.make_table_complete(CP, chroms_bg)
        
        # get the pvalues
        CPval=annotator.estimate_pvals(GP,CS,CP)
        jobcount+=1
        
        # open outfile 
        info('#%d write a R script of ChIP region annotation...' %jobcount)
        ofhd=open(options.name+'.R','w')
        pdfname=options.name+'.pdf'
        # the first part of CEAS R script. Because wig profiling is not run, just terminate
        rscript = options.argtxt
        rscript += "\n"
        rscript += R.pdf(pdfname,height=11.5,width=8.5)   
        rscript += inout.draw_CEAS(GP,CP,CPval,bg_res=options.bg_res,chip_res=options.chip_res,prom=options.sizes,biprom=options.bisizes,down=options.sizes,gene_div=(3,5))      
        ofhd.write(rscript)    # write ChIP region annotation
        # write the pie chart
        GEP = GenomePieP.export2dic()
        rscript = inout.draw_pie_distribution_of_elements(GEP, CEP, gprom=(1000, 2000, 3000), gdown=(1000, 2000, 3000), prom=options.sizes, down=options.sizes)
        ofhd.write(rscript)
        # draw ChIP regions over chromosomes if no wig profiling is going on
        if not options.pf:
            # if no scores are given, just put 1 as dummy scores
            if not inout.check_if_yes_score(ChIP):
                inout.fill_up_scores_w_val(ChIP, val = 1)
            rscript = inout.draw_ChIP_over_genome_mono_col(ChIP, None, n_peaks=options.n_peaks)
            ofhd.write(rscript)
        jobcount+=1
        
    # when wig profiling is running
    if options.pf:
        if options.rebg:
            GenomeBGS=tables.Summary(name='GenomeBGS')
            GenomePieS= tables.PieSummary( name='GenomePieS' )
        
        # if gene groups are give
        if options.gn_groups:
            #subsets=inout.read_gene_subsets(options.gn_groups)
            subsets = inout.read_gene_subsets2(options.gn_groups)

        chrom=''
        chrcount=1
        prof=profiler.WigProfiler2(rel_dist=options.rel_dist, step=options.pf_res, metagenesize=options.metagene_size, \
                                   catexonsize=options.catexon_size, catintronsize=options.catintron_size, metaexonsizes=options.metaexonsizes, \
                                   metaintronsizes=options.metaintronsizes, elowers=options.elowers, euppers=options.euppers, ilowers=options.ilowers, \
                                   iuppers=options.iuppers)
        wigsize = {}
        swig=inout.Wig()
        ws = sampler.WigSamplerFast()
        FIRST=True
        fixedStep = False		# variableStep == True: variableStep Wig, False: fixedStep
        for line in open(options.wig,'r').xreadlines():
            if not line: continue
            # when meet 'track', continue to the next line
            if re.search(r'track',line): 
                try:
                    description=re.search(r'description="(\w+)"\s',line).group(1)
                except AttributeError:
                    pass
                continue
            
            # check if fixedStep or variableStep
            if re.search(r'fixedStep', line):
                fixedStep = True
                step = int(re.search(r'step=(\S+)\s', line).group(1))
                position = int(re.search(r'start=(\S+)\s', line).group(1))
            
            # get the chromosome
            if re.search(r'chrom=(\S+)\s', line):
                newchrom=re.search(r'chrom=(\S+)\s',line).group(1)
                try:
                    newchrom=inout.standard_chroms[newchrom]
                except KeyError:
                    pass
                continue
            
            # split the line
            l=line.strip().split()
        
        # the beginning
            if chrom=='' and chrom!=newchrom:
                # if the chromosome is not in gene table, continue
                chrom=newchrom
                if chrom in chroms_GeneT: # only if the new chromosome is in the chroms of gene table, a wig object is initiated.
                    if options.rebg:
                        info("#%d-%d run wig profiling and genome bg annotation of %s..." %(jobcount,chrcount,chrom))
                    else:
                        info("#%d-%d run wig profiling of %s..." %(jobcount,chrcount,chrom))
                    
                    input=inout.Wig()
                    
                    # if fixedStep, calculate the position from start and step
                    if fixedStep:
                        row = [position, l[-1]]
                        position += step
                    else:
                   	    row = l
                   	
                   	# add the new line to the Wig object
                    input.add_line(chrom, row)
                    chrcount+=1
            elif chrom!='' and chrom!=newchrom:    # new chromosome
                if chrom in chroms_GeneT:

                    # do genome BG annotation
                    if options.rebg:
                        Sampler=sampler.GenomeSampler()
                        Annotator=annotator.Annotator()
                        GA=Annotator.annotate(Sampler.sample(input,resolution=options.bg_res),GeneT,roi=roi,prom=options.sizes,biprom=options.bisizes,down=options.sizes,gene_div=(3,5))
                        tempS,tempP=Annotator.summarize(GA)
                        GenomeBGS.add_row(chrom,tempS.get_row(chrom))

                        tempPieSt = Annotator.obtain_distribution_of_sites_per_chrom(GA)
                        tempPieS = tables.PieSummary()
                        tempPieS.import2tb( tempPieSt )
                        GenomePieS.add_row( chrom, tempPieS.get_row( chrom ) )
                    
                    # update ChIP regions with WIG scores
                    if options.chipannot:
                        profiler.scan_scores_in_wig(ChIP, input)                                        
                    try:
                        wigsize[chrom] = (input[chrom][0][0], input[chrom][0][-1])
                    except IndexError:
                        wigsize[chrom] = (0, 0)
                                                                 
                    # wig profiling
                    profiles = prof.profile(input, GeneT)
        
                    # get average of this chromosome
                    avg_up,upcount=corelib.mean_col_by_col(profiles['upstreams'], counts=True)
                    avg_down,downcount=corelib.mean_col_by_col(profiles['downstreams'], counts=True)
                    avg_mg,genecount=corelib.mean_col_by_col(profiles['genes'], counts=True)
                    avg_mce,cexoncount=corelib.mean_col_by_col(profiles['catexons'], counts=True)
                    avg_mci,cintroncount=corelib.mean_col_by_col(profiles['catintrons'],counts=True)
                    # get average of exon and introns of this chromosome
                    n_metas = len(profiles['exons'])
                    outs = map(corelib.mean_col_by_col, map(corelib.extend_list_series, profiles['exons']), [True] * len(profiles['exons']))
                    avg_me = map(operator.itemgetter(0), outs)
                    exoncount = map(operator.itemgetter(1), outs)
                    outs = map(corelib.mean_col_by_col, map(corelib.extend_list_series, profiles['introns']), [True] * len(profiles['introns']))
                    avg_mi = map(operator.itemgetter(0), outs)
                    introncount = map(operator.itemgetter(1), outs)
                    del outs

                    if options.dump: # if dump is on, dump upstream, downstream and genebody
                        if options.name2:
                            options.out['upstreams'].write(profiler.dump(chrom, profiles['start'], profiles['end'], profiles['name2'], profiles['strand'], profiles['upstreams']))
                            options.out['downstreams'].write(profiler.dump(chrom, profiles['start'], profiles['end'], profiles['name2'], profiles['strand'], profiles['downstreams']))
                            options.out['genes'].write(profiler.dump(chrom, profiles['start'], profiles['end'], profiles['name2'], profiles['strand'], profiles['genes']))
                        else:
                            options.out['upstreams'].write(profiler.dump(chrom, profiles['start'], profiles['end'], profiles['name'], profiles['strand'], profiles['upstreams']))
                            options.out['downstreams'].write(profiler.dump(chrom, profiles['start'], profiles['end'], profiles['name'], profiles['strand'], profiles['downstreams']))
                            options.out['genes'].write(profiler.dump(chrom, profiles['start'], profiles['end'], profiles['name'], profiles['strand'], profiles['genes']))
            
                    if not FIRST:    # if not first chromosome
                        avg_upstream,avg_upcount=corelib.weight_mean_col_by_col([avg_upstream,avg_up],[avg_upcount,upcount],counts=True)
                        avg_downstream,avg_downcount=corelib.weight_mean_col_by_col([avg_downstream,avg_down],[avg_downcount,upcount],counts=True)
                        avg_metagene,avg_genecount=corelib.weight_mean_col_by_col([avg_metagene,avg_mg],[avg_genecount,genecount],counts=True)
                        avg_metacatexon,avg_cexoncount=corelib.weight_mean_col_by_col([avg_metacatexon,avg_mce],[avg_cexoncount,cexoncount],counts=True)
                        avg_metacatintron,avg_cintroncount=corelib.weight_mean_col_by_col([avg_metacatintron,avg_mci],[avg_cintroncount,cintroncount],counts=True)
                        outs = map(corelib.weight_mean_col_by_col, map(lambda x, y: [x, y], avg_metaexon, avg_me),  map(lambda x, y: [x, y], avg_exoncount, exoncount), [True] * n_metas)
                        avg_metaexon = map(operator.itemgetter(0), outs)
                        avg_exoncount = map(operator.itemgetter(1), outs)
                        outs = map(corelib.weight_mean_col_by_col, map(lambda x, y: [x, y], avg_metaintron, avg_mi), map(lambda x, y: [x, y], avg_introncount, introncount), [True] * n_metas)
                        avg_metaintron = map(operator.itemgetter(0), outs)
                        avg_introncount = map(operator.itemgetter(1), outs)
                        del outs, avg_me, avg_mi, exoncount, introncount
                        del avg_up,avg_down,avg_mg,avg_mce,avg_mci,upcount,downcount,genecount,cexoncount,cintroncount

                        if options.gn_groups:    # when gene sub-gropus are given
                            if options.name2:
                                ixs, subsets = profiler.get_gene_indicies(profiles['name2'], subsets)
                                #ixs  = profiler.get_gene_indicies2(profiles['name2'], subsets)
                            else:
                                ixs, subsets = profiler.get_gene_indicies(profiles['name'], subsets)
                                #ixs  = profiler.get_gene_indicies2(profiles['name'], subsets)
            
                            avg_ups, upcs = profiler.select_take_average_profiles_chr_by_chr(ixs, profiles['upstreams'])
                            avg_downs, downcs = profiler.select_take_average_profiles_chr_by_chr(ixs, profiles['downstreams'])
                            avg_mgs, gcs = profiler.select_take_average_profiles_chr_by_chr(ixs, profiles['genes'])
                            avg_mces, cecs = profiler.select_take_average_profiles_chr_by_chr(ixs, profiles['catexons'])
                            avg_mcis, cics = profiler.select_take_average_profiles_chr_by_chr(ixs, profiles['catintrons'])
                            # exon and intron profile
                            avg_mes, ecs = profiler.select_take_average_profiles_chr_by_chr_meta(ixs, profiles['exons'])
                            avg_mis, ics = profiler.select_take_average_profiles_chr_by_chr_meta(ixs, profiles['introns'])
                        
                            avg_upstreams,avg_upcounts=profiler.weight_mean_profiles_chr_by_chr(avg_upstreams,avg_upcounts,avg_ups,upcs)
                            avg_downstreams,avg_downcounts=profiler.weight_mean_profiles_chr_by_chr(avg_downstreams,avg_downcounts,avg_downs,downcs)
                            avg_metagenes,avg_genecounts=profiler.weight_mean_profiles_chr_by_chr(avg_metagenes,avg_genecounts,avg_mgs,gcs)
                            avg_metacatexons,avg_cexoncounts=profiler.weight_mean_profiles_chr_by_chr(avg_metacatexons,avg_cexoncounts,avg_mces,cecs)
                            avg_metacatintrons,avg_cintroncounts=profiler.weight_mean_profiles_chr_by_chr(avg_metacatintrons,avg_cintroncounts,avg_mcis,cics)
                            # exon and intron profiling
                            avg_metaexons, avg_exoncounts = profiler.weight_mean_profiles_chr_by_chr_meta(avg_metaexons, avg_exoncounts, avg_mes, ecs)
                            avg_metaintrons, avg_introncounts = profiler.weight_mean_profiles_chr_by_chr_meta(avg_metaintrons, avg_introncounts, avg_mis, ics)
                            del avg_mes,avg_mis, ecs,ics
                            del avg_ups,avg_downs,avg_mgs,avg_mces,avg_mcis,upcs,downcs,gcs,cecs,cics
                            
                            if options.dump: # if dump is on, dump upstream, downstream and genebody
                                if options.name2:
                                    txts = profiler.dump_mult(chrom, profiles['start'], profiles['end'], profiles['name2'], profiles['strand'], profiles['upstreams'], ixs)
                                    write_txts(options.outs['upstreams'], txts)
                                    txts = profiler.dump_mult(chrom, profiles['start'], profiles['end'], profiles['name2'], profiles['strand'], profiles['downstreamss'], ixs)
                                    write_txts(options.outs['downstreams'], txts)
                                    txts = profiler.dump_mult(chrom, profiles['start'], profiles['end'], profiles['name2'], profiles['strand'], profiles['genes'], ixs)
                                    write_txts(options.outs['genes'], txts)
                                else:
                                    txts = profiler.dump_mult(chrom, profiles['start'], profiles['end'], profiles['name'], profiles['strand'], profiles['upstreams'], ixs)
                                    write_txts(options.outs['upstreams'], txts)
                                    txts = profiler.dump_mult(chrom, profiles['start'], profiles['end'], profiles['name'], profiles['strand'], profiles['downstreams'], ixs)
                                    write_txts(options.outs['downstreams'], txts)
                                    txts = profiler.dump_mult(chrom, profiles['start'], profiles['end'], profiles['name'], profiles['strand'], profiles['genes'], ixs)
                                    write_txts(options.outs['genes'], txts)
                                    
                
                    else:   # if first chromosome
                        avg_upstream = avg_up
                        avg_downstream = avg_down
                        avg_metagene = avg_mg
                        avg_metacatexon = avg_mce
                        avg_metacatintron = avg_mci
                        avg_upcount = upcount
                        avg_downcount = downcount
                        avg_genecount = genecount
                        avg_cexoncount = cexoncount
                        avg_cintroncount = cintroncount
                        
                        avg_metaexon = avg_me
                        avg_metaintron = avg_mi
                        avg_exoncount=exoncount
                        avg_introncount=introncount

                        if options.gn_groups:
                            # if name2 is used instead of name
                            if options.name2:
                                ixs, subsets = profiler.get_gene_indicies(profiles['name2'], subsets)
                                #ixs = profiler.get_gene_indicies2(profiles['name2'], subsets)
                            else:
                                ixs, subsets = profiler.get_gene_indicies(profiles['name'], subsets)
                                #ixs = profiler.get_gene_indicies2(profiles['name'], subsets)

                            avg_upstreams, avg_upcounts = profiler.select_take_average_profiles_chr_by_chr(ixs, profiles['upstreams'])
                            avg_downstreams, avg_downcounts = profiler.select_take_average_profiles_chr_by_chr(ixs, profiles['downstreams'])
                            avg_metagenes, avg_genecounts = profiler.select_take_average_profiles_chr_by_chr(ixs, profiles['genes'])
                            avg_metacatexons, avg_cexoncounts = profiler.select_take_average_profiles_chr_by_chr(ixs, profiles['catexons'])
                            avg_metacatintrons, avg_cintroncounts = profiler.select_take_average_profiles_chr_by_chr(ixs, profiles['catintrons'])
                            
                            avg_metaexons, avg_exoncounts = profiler.select_take_average_profiles_chr_by_chr_meta(ixs, profiles['exons'])
                            avg_metaintrons, avg_introncounts = profiler.select_take_average_profiles_chr_by_chr_meta(ixs, profiles['introns'])
                            
                            if options.dump: # if dump is on, dump upstream, downstream and genebody
                                if options.name2:
                                    txts = profiler.dump_mult(chrom, profiles['start'], profiles['end'], profiles['name2'], profiles['strand'], profiles['upstreams'], ixs)
                                    write_txts(options.outs['upstreams'], txts)
                                    txts = profiler.dump_mult(chrom, profiles['start'], profiles['end'], profiles['name2'], profiles['strand'], profiles['downstreams'], ixs)
                                    write_txts(options.outs['downstreams'], txts)
                                    txts = profiler.dump_mult(chrom, profiles['start'], profiles['end'], profiles['name2'], profiles['strand'], profiles['genes'], ixs)
                                    write_txts(options.outs['genes'], txts)
                                else:
                                    txts = profiler.dump_mult(chrom, profiles['start'], profiles['end'], profiles['name'], profiles['strand'], profiles['upstreams'], ixs)
                                    write_txts(options.outs['upstreams'], txts)
                                    txts = profiler.dump_mult(chrom, profiles['start'], profiles['end'], profiles['name'], profiles['strand'], profiles['downstreams'], ixs)
                                    write_txts(options.outs['downstreams'], txts)
                                    txts = profiler.dump_mult(chrom, profiles['start'], profiles['end'], profiles['name'], profiles['strand'], profiles['genes'], ixs)
                                    write_txts(options.outs['genes'], txts)
                        
                        FIRST=False
                
                    ##del profiles  
                
                # set chrom to the new chromosome
                chrom=newchrom
                if chrom in chroms_GeneT:    # only if the new chromosome is in the chroms of gene table, a wig object is initiated.
                    if options.rebg:
                        info("#%d-%d run wig profiling and genome bg annotation of %s..." %(jobcount,chrcount,chrom))
                    else:
                        info("#%d-%d run wig profiling of %s..." %(jobcount,chrcount,chrom))
                    
                    input=inout.Wig()
                    
                    # if fixedStep, calculate the position from start and step
                    if fixedStep:
                	    row = [position, l[-1]]
                	    position += step
                    else:
                	    row = l
                	    
                	# add the line to the Wig object
                    input.add_line(chrom, row)
                    chrcount+=1
            else:    # in the middle of chromosome
                if chrom in chroms_GeneT:   # only if the new chromosome is in the chroms of gene table, the wig object is updated.
                    # if fixedStep, calculate the position from start and step
                    if fixedStep:
                        row = [position, l[-1]]
                        position += step
                    else:
                        row = l
                        
                    # add the line to the Wig object    
                    input.add_line(chrom, row)
                        
    # do profiling for the last chromosome 
        if chrom in chroms_GeneT:
            
            if options.rebg:
                Sampler=sampler.GenomeSampler()
                Annotator=annotator.Annotator()
                GA=Annotator.annotate(Sampler.sample(input,resolution=options.bg_res),GeneT,roi=roi,prom=options.sizes,biprom=options.bisizes,down=options.sizes,gene_div=(3,5))
                tempS,tempP=Annotator.summarize(GA)
                GenomeBGS.add_row(chrom,tempS.get_row(chrom))

                tempPieSt = Annotator.obtain_distribution_of_sites_per_chrom(GA)
                tempPieS = tables.PieSummary()
                tempPieS.import2tb( tempPieSt )
                GenomePieS.add_row( chrom, tempPieS.get_row( chrom ) )
                
                # if extra bed file exists, get the bg statistics of the regions
                if options.chipannot and options.ebedannot:
                    GP=_get_bgroi(GP,GenomeBGS,roi=roi,bg_res=options.bg_res)
            
            # update ChIP regions with wig scores
            if options.chipannot:
                profiler.scan_scores_in_wig(ChIP, input)
            
            try:
                wigsize[chrom] = (input[chrom][0][0], input[chrom][0][-1])
            except IndexError:
                wigsize[chrom] = (0, 0)
                                
            # profiling
            profiles = prof.profile(input, GeneT)
            del input 
            
            # get average of this chromosome
            avg_up,upcount = corelib.mean_col_by_col(profiles['upstreams'], counts=True)
            avg_down,downcount = corelib.mean_col_by_col(profiles['downstreams'], counts=True)
            avg_mg,genecount = corelib.mean_col_by_col(profiles['genes'], counts=True)
            avg_mce,cexoncount = corelib.mean_col_by_col(profiles['catexons'], counts=True)
            avg_mci,cintroncount = corelib.mean_col_by_col(profiles['catintrons'],counts=True)
            # get average of exons and introns of this chromosome
            n_metas = len(profiles['exons'])  
            outs = map(corelib.mean_col_by_col, map(corelib.extend_list_series, profiles['exons']), [True] * len(profiles['exons']))
            avg_me = map(operator.itemgetter(0), outs)
            exoncount = map(operator.itemgetter(1), outs)
            outs = map(corelib.mean_col_by_col, map(corelib.extend_list_series, profiles['introns']), [True] * len(profiles['introns']))
            avg_mi = map(operator.itemgetter(0), outs)
            introncount = map(operator.itemgetter(1), outs)

            if options.dump: # if dump is on, dump upstream, downstream and genebody
                if options.name2:
                    options.out['upstreams'].write(profiler.dump(chrom, profiles['start'], profiles['end'], profiles['name2'], profiles['strand'], profiles['upstreams']))
                    options.out['downstreams'].write(profiler.dump(chrom, profiles['start'], profiles['end'], profiles['name2'], profiles['strand'], profiles['downstreams']))
                    options.out['genes'].write(profiler.dump(chrom, profiles['start'], profiles['end'], profiles['name2'], profiles['strand'], profiles['genes']))
                else:
                    options.out['upstreams'].write(profiler.dump(chrom, profiles['start'], profiles['end'], profiles['name'], profiles['strand'], profiles['upstreams']))
                    options.out['downstreams'].write(profiler.dump(chrom, profiles['start'], profiles['end'], profiles['name'], profiles['strand'], profiles['downstreams']))
                    options.out['genes'].write(profiler.dump(chrom, profiles['start'], profiles['end'], profiles['name'], profiles['strand'], profiles['genes']))
            
            if not FIRST:    # the first chromosome profiling
                avg_upstream,avg_upcount=corelib.weight_mean_col_by_col([avg_upstream,avg_up],[avg_upcount,upcount],counts=True)
                avg_downstream,avg_downcount=corelib.weight_mean_col_by_col([avg_downstream,avg_down],[avg_downcount,upcount],counts=True)
                avg_metagene,avg_genecount=corelib.weight_mean_col_by_col([avg_metagene,avg_mg],[avg_genecount,genecount],counts=True)
                avg_metacatexon,avg_cexoncount=corelib.weight_mean_col_by_col([avg_metacatexon,avg_mce],[avg_cexoncount,cexoncount],counts=True)
                avg_metacatintron,avg_cintroncount=corelib.weight_mean_col_by_col([avg_metacatintron,avg_mci],[avg_cintroncount,cintroncount],counts=True)            
                # get the average of exon and introns of all together
                outs = map(corelib.weight_mean_col_by_col, map(lambda x, y: [x, y], avg_metaexon, avg_me),  map(lambda x, y: [x, y], avg_exoncount, exoncount), [True] * n_metas)
                avg_metaexon = map(operator.itemgetter(0), outs)
                avg_exoncount = map(operator.itemgetter(1), outs)
                outs = map(corelib.weight_mean_col_by_col, map(lambda x, y: [x, y], avg_metaintron, avg_mi), map(lambda x, y: [x, y], avg_introncount, introncount), [True] * n_metas)
                avg_metaintron = map(operator.itemgetter(0), outs)
                avg_introncount = map(operator.itemgetter(1), outs)
                del outs,avg_me,avg_mi,exoncount,introncount
                del avg_up,avg_down,avg_mg,avg_mce,avg_mci,upcount,downcount,genecount,cexoncount,cintroncount
                
                if options.gn_groups:
                    # when name2 is used instead of name of the gene annotation table
                    if options.name2:
                        ixs, subsets = profiler.get_gene_indicies(profiles['name2'], subsets)
                        #ixs = profiler.get_gene_indicies2(profiles['name2'], subsets)
                    else:
                        ixs, subsets = profiler.get_gene_indicies(profiles['name'], subsets)
                        #ixs = profiler.get_gene_indicies2(profiles['name'], subsets)
                    
                    # take an average of each profile (upstream, downstream, gene, exon, intron, cat-exon, cat-intron
                    avg_ups, upcs = profiler.select_take_average_profiles_chr_by_chr(ixs, profiles['upstreams'])
                    avg_downs, downcs = profiler.select_take_average_profiles_chr_by_chr(ixs, profiles['downstreams'])
                    avg_mgs, gcs = profiler.select_take_average_profiles_chr_by_chr(ixs, profiles['genes'])
                    avg_mces, cecs = profiler.select_take_average_profiles_chr_by_chr(ixs, profiles['catexons'])
                    avg_mcis, cics = profiler.select_take_average_profiles_chr_by_chr(ixs, profiles['catintrons'])
                    # take averages of exons and introns of all the chromosomes
                    avg_mes, ecs = profiler.select_take_average_profiles_chr_by_chr_meta(ixs, profiles['exons'])
                    avg_mis, ics = profiler.select_take_average_profiles_chr_by_chr_meta(ixs, profiles['introns'])
    
                    avg_upstreams,avg_upcounts=profiler.weight_mean_profiles_chr_by_chr(avg_upstreams,avg_upcounts,avg_ups,upcs)
                    avg_downstreams,avg_downcounts=profiler.weight_mean_profiles_chr_by_chr(avg_downstreams,avg_downcounts,avg_downs,downcs)
                    avg_metagenes,avg_genecounts=profiler.weight_mean_profiles_chr_by_chr(avg_metagenes,avg_genecounts,avg_mgs,gcs)
                    avg_metacatexons,avg_cexoncounts=profiler.weight_mean_profiles_chr_by_chr(avg_metacatexons,avg_cexoncounts,avg_mces,cecs)
                    avg_metacatintrons,avg_cintroncounts=profiler.weight_mean_profiles_chr_by_chr(avg_metacatintrons,avg_cintroncounts,avg_mcis,cics)
                    
                    avg_metaexons, avg_exoncounts = profiler.weight_mean_profiles_chr_by_chr_meta(avg_metaexons, avg_exoncounts, avg_mes, ecs)
                    avg_metaintrons, avg_introncounts = profiler.weight_mean_profiles_chr_by_chr_meta(avg_metaintrons, avg_introncounts, avg_mis, ics)
                    del avg_mes,avg_mis,ecs,ics
                    del avg_ups,avg_downs,avg_mgs,avg_mces,avg_mcis,upcs,downcs,gcs,cecs,cics
                    
                    if options.dump: # if dump is on, dump upstream, downstream and genebody
                        if options.name2:
                            txts = profiler.dump_mult(chrom, profiles['start'], profiles['end'], profiles['name2'], profiles['strand'], profiles['upstreams'], ixs)
                            write_txts(options.outs['upstreams'], txts)
                            txts = profiler.dump_mult(chrom, profiles['start'], profiles['end'], profiles['name2'], profiles['strand'], profiles['downstreams'], ixs)
                            write_txts(options.outs['downstreams'], txts)
                            txts = profiler.dump_mult(chrom, profiles['start'], profiles['end'], profiles['name2'], profiles['strand'], profiles['genes'], ixs)
                            write_txts(options.outs['genes'], txts)
                        else:
                            txts = profiler.dump_mult(chrom, profiles['start'], profiles['end'], profiles['name'], profiles['strand'], profiles['upstreams'], ixs)
                            write_txts(options.outs['upstreams'], txts)
                            txts = profiler.dump_mult(chrom, profiles['start'], profiles['end'], profiles['name'], profiles['strand'], profiles['downstreams'], ixs)
                            write_txts(options.outs['downstreams'], txts)
                            txts = profiler.dump_mult(chrom, profiles['start'], profiles['end'], profiles['name'], profiles['strand'], profiles['genes'], ixs)
                            write_txts(options.outs['genes'], txts)
                    
            else:
                avg_upstream=avg_up
                avg_downstream=avg_down
                avg_metagene=avg_mg
                avg_metacatexon=avg_mce
                avg_metacatintron=avg_mci
                avg_upcount=upcount
                avg_downcount=downcount
                avg_genecount=genecount
                avg_cexoncount=cexoncount
                avg_cintroncount=cintroncount
                
                avg_metaexon=avg_me
                avg_metaintron=avg_mi
                avg_exoncount=exoncount
                avg_introncount=introncount

                if options.gn_groups:
                    if options.name2:
                        ixs, subsets = profiler.get_gene_indicies(profiles['name2'], subsets)
                        #ixs = profiler.get_gene_indicies2(profiles['name2'], subsets)
                    else:
                        ixs, subsets = profiler.get_gene_indicies(profiles['name'], subsets)
                        #ixs = profiler.get_gene_indicies2(profiles['name'], subsets)
                    
                    avg_upstreams, avg_upcounts = profiler.select_take_average_profiles_chr_by_chr(ixs, profiles['upstreams'])
                    avg_downstreams, avg_downcounts = profiler.select_take_average_profiles_chr_by_chr(ixs, profiles['downstreams'])
                    avg_metagenes, avg_genecounts = profiler.select_take_average_profiles_chr_by_chr(ixs, profiles['genes'])
                    avg_metacatexons, avg_cexoncounts = profiler.select_take_average_profiles_chr_by_chr(ixs, profiles['catexons'])
                    avg_metacatintrons, avg_cintroncounts = profiler.select_take_average_profiles_chr_by_chr(ixs, profiles['catintrons'])
                    
                    avg_metaexons, avg_exoncounts = profiler.select_take_average_profiles_chr_by_chr_meta(ixs, profiles['exons'])
                    avg_metaintrons, avg_introncounts = profiler.select_take_average_profiles_chr_by_chr_meta(ixs, profiles['introns'])
                    
                    if options.dump: # if dump is on, dump upstream, downstream and genebody
                        if options.name2:
                            txts = profiler.dump_mult(chrom, profiles['start'], profiles['end'], profiles['name2'], profiles['strand'], profiles['upstreams'], ixs)
                            write_txts(options.outs['upstreams'], txts)
                            txts = profiler.dump_mult(chrom, profiles['start'], profiles['end'], profiles['name2'], profiles['strand'], profiles['downstreams'], ixs)
                            write_txts(options.outs['downstreams'], txts)
                            txts = profiler.dump_mult(chrom, profiles['start'], profiles['end'], profiles['name2'], profiles['strand'], profiles['genes'], ixs)
                            write_txts(options.outs['genes'], txts)
                        else:
                            txts = profiler.dump_mult(chrom, profiles['start'], profiles['end'], profiles['name'], profiles['strand'], profiles['upstreams'], ixs)
                            write_txts(options.outs['upstreams'], txts)
                            txts = profiler.dump_mult(chrom, profiles['start'], profiles['end'], profiles['name'], profiles['strand'], profiles['downstreams'], ixs)
                            write_txts(options.outs['downstreams'], txts)
                            txts = profiler.dump_mult(chrom, profiles['start'], profiles['end'], profiles['name'], profiles['strand'], profiles['genes'], ixs)
                            write_txts(options.outs['genes'], txts)

        # close the file handlers for dumping the profiles
        if options.dump:
            close_dump_files(options.out['upstreams'])
            close_dump_files(options.out['downstreams'])
            close_dump_files(options.out['genes'])
            
            if options.gn_groups:
                close_dump_mult_files(options.outs['upstreams'])
                close_dump_mult_files(options.outs['downstreams'])
                close_dump_mult_files(options.outs['genes'])

        jobcount+=1
        
        if options.chipannot and options.rebg:
            info('#%d perform ChIP region annotation...' %jobcount)

            # summarize the genome annotation
            GenomeBGS.summarize()
            GP=GenomeBGS.get_p()
            GP.set_name('GenomeBGP')

            GenomePieS.summarize()
            GenomePieP = GenomePieS.get_p()
            GenomePieP.set_name('GenomePieP')

            # do ChIP annotation
            Annot=annotator.Annotator()
            ChIPA=Annot.annotate(genome_coordinates=ChIPSamp,gene_table=GeneT,roi=roi,prom=options.sizes,biprom=options.bisizes,down=options.sizes,gene_div=(3,5))
            CS,CP=Annot.summarize(ChIPA)
            CPval=annotator.estimate_pvals(GP,CS,CP)
            CES, CEP = Annot.obtain_distribution_of_sites(ChIPA)
            jobcount+=1
            
            info('#%d write R script of ChIP region annotation and wig profiling...' %jobcount)
            ofhd=open(options.name+'.R','w')
            pdfname=options.name+'.pdf'
            # the first part of CEAS R script. Because wig profiling is not run, just terminate
            rscript = options.argtxt
            rscript += "\n"
            rscript += R.pdf(pdfname,height=11.5,width=8.5)
            rscript+=inout.draw_CEAS(GP,CP,CPval,bg_res=options.bg_res,chip_res=options.chip_res,prom=options.sizes,biprom=options.bisizes,down=options.sizes,gene_div=(3,5)) 
            ofhd.write(rscript)    # writing ChIP region annotation
            # write the pie chart
            GEP = GenomePieP.export2dic()
            rscript = inout.draw_pie_distribution_of_elements(GEP, CEP, gprom=(1000, 2000, 3000), gdown=(1000, 2000, 3000), prom=options.sizes, down=options.sizes)
            ofhd.write(rscript)
            jobcount+=1
        
        # 
        # writing R script for wig profiling
        #
        if options.chipannot:
            # write the ChIP regions over chromosomes
            rscript = inout.draw_ChIP_over_genome_mono_col(ChIP, wigsize, n_peaks=options.n_peaks)
            ofhd.write(rscript)
            info('#%d append an R script of wig profiling...' %jobcount)
        else:
            info('#%d write an R script of wig profiling...' %jobcount)
            ofhd = open(options.name+'.R', 'w')
            pdfname = options.name + '.pdf'
            rscript = options.argtxt
            rscript += "\n"
            rscript += R.pdf(pdfname, height=11.5, width=8.5)
            ofhd.write(rscript)
        
        #
        # write a R script of drawing profiles near genes
        #
    
        # get breaks for the plots
        breaks = profiles['breaks']
        metagene_breaks = profiles['genebreaks']
        metacatexon_breaks = profiles['catexonbreaks']
        metacatintron_breaks = profiles['catintronbreaks']
        metaexon_breaks = profiles['exonbreaks']
        metaintron_breaks = profiles['intronbreaks']
         
        # write R script
        if options.gn_groups:       # when multiple gene groups are given
            # append the profiles of all genes
            avg_upstreams.append(avg_upstream)
            avg_downstreams.append(avg_downstream)
            avg_metagenes.append(avg_metagene)
            avg_metacatexons.append(avg_metacatexon)
            avg_metacatintrons.append(avg_metacatintron)
            rscript=inout.draw_profile_plots(breaks,avg_upstreams,avg_downstreams,metagene_breaks,avg_metagenes,metacatexon_breaks,avg_metacatexons,metacatintron_breaks,avg_metacatintrons,metagene_breaks_lim=[-1000,1000],legends=options.gn_names)
            # exon intron profiling
            map(lambda x, y: x.append(y), avg_metaexons, avg_metaexon)
            map(lambda x, y: x.append(y), avg_metaintrons, avg_metaintron)
            rscript+=inout.draw_exon_intron_profile_plots(metaexon_breaks, avg_metaexons,metaintron_breaks,avg_metaintrons, options.elowers, options.euppers, options.ilowers, options.iuppers, legends=options.gn_names)
        else:                       # only when a single master profiling is obtianed
            rscript=inout.draw_profile_plot(breaks,avg_upstream,avg_downstream,metagene_breaks,avg_metagene,metacatexon_breaks,avg_metacatexon,metacatintron_breaks,avg_metacatintron,metagene_breaks_lim=[-1000,1000])
            
            rscript+=inout.draw_exon_intron_profile_plot(metaexon_breaks,avg_metaexon,metaintron_breaks,avg_metaintron, options.elowers, options.euppers, options.ilowers, options.iuppers)
    
        ofhd.write(rscript)    # write wig profiling
    
    # write to the file and close it
    ofhd.write(R.devoff())
    ofhd.close()
    
    # Run R directly - if any exceptions, just pass
    try:
        p = subprocess.Popen("R" + " --vanilla < %s"  %(options.name+'.R'), shell=True)
        #sts = os.waitpid(p.pid, 0)
        p.wait()
        info ('#... cong! See %s for the graphical results of CEAS!' %(options.name+'.pdf'))
    except:
        info ('#... cong! Run %s using R for the graphical results of CEAS! CEAS could not run R directly.' %(options.name+'.R'))
    	
    	
# ------------------------------------
# functions
# ------------------------------------
  
def prepare_optparser ():
    """Prepare optparser object. New options will be added in this
    function first.
    
    """
    
    usage = "usage: %prog < input files > [options]"
    description = "CEAS (Cis-regulatory Element Annotation System)"
    
    optparser = OptionParser(version="%prog -- 0.9.9.7 (package version 1.0.2)",description=description,usage=usage,add_help_option=False)
    optparser.add_option("-h","--help",action="help",help="Show this help message and exit.")
    optparser.add_option("-b","--bed",dest="bed",type="string",
                         help="BED file of ChIP regions.")
    optparser.add_option("-w","--wig",dest="wig",type="string",
                         help="WIG file for either wig profiling or genome background annotation. WARNING: --bg flag must be set for genome background re-annotation.")
    optparser.add_option("-e","--ebed",dest="ebed",type="string",
                         help="BED file of extra regions of interest (eg, non-coding regions)")
    optparser.add_option("-g","--gt",dest="gdb",type="string",
                         help="Gene annotation table (eg, a refGene table in sqlite3 db format provided through the CEAS web, http://liulab.dfci.harvard.edu/CEAS/download.html).")
    optparser.add_option("--name",dest="name",\
                         help="Experiment name. This will be used to name the output files. If an experiment name is not given, the stem of the input BED file name will be used instead (eg, if 'peaks.bed', 'peaks' will be used as a name.)")
    optparser.add_option("--sizes",dest="sizes",type="str",
                         help="Promoter (also dowsntream) sizes for ChIP region annotation. Comma-separated three values or a single value can be given. If a single value is given, it will be segmented into three equal fractions (ie, 3000 is equivalent to 1000,2000,3000), DEFAULT: 1000,2000,3000. WARNING: Values > 10000bp are automatically set to 10000bp.", default='1000,2000,3000')    
    optparser.add_option("--bisizes",dest="bisizes",type="str",
                         help="Bidirectional-promoter sizes for ChIP region annotation Comma-separated two values or a single value can be given. If a single value is given, it will be segmented into two equal fractions (ie, 5000 is equivalent to 2500,5000) DEFAULT: 2500,5000bp. WARNING: Values > 20000bp are automatically set to 20000bp.", default='2500,5000')  
    optparser.add_option("--bg",action="store_true",dest="bg",\
                         help="Run genome BG annotation again. WARNING: This flag is effective only if a WIG file is given through -w (--wig). Otherwise, ignored.",default=False)
    optparser.add_option("--span", dest="span", type="int",\
                         help="Span from TSS and TTS in the gene-centered annotation. ChIP regions within this range from TSS and TTS are considered when calculating the coverage rates in promoter and downstream, DEFAULT=3000bp", default=3000)         
    optparser.add_option("--pf-res", dest="pf_res", type="int",\
                          help="Wig profiling resolution, DEFAULT: 50bp. WARNING: Value smaller than the wig interval (resolution) may cause aliasing error.", default=50) 
    optparser.add_option("--rel-dist",dest="rel_dist",type="int",
                         help="Relative distance to TSS/TTS in wig profiling, DEFAULT: 3000bp", default=3000)   
    optparser.add_option("--gn-groups",dest="gn_groups",type="string",\
                         help="Gene-groups of particular interest in wig profiling. Each gene group file must have gene names in the 1st column. The file names are separated by commas w/ no space (eg, --gn-groups=top10.txt,bottom10.txt)") 
    optparser.add_option("--gn-group-names", dest="gn_names",type="string",\
                         help="The names of the gene groups in --gn-groups. The gene group names are separated by commas. (eg, --gn-group-names='top 10%,bottom 10%'). These group names appear in the legends of the wig profiling plots. If no group names given, the groups are represented as 'Group 1, Group2,...Group n'.")
    optparser.add_option("--gname2", action="store_true", dest="name2",\
                         help="Whether or not use the 'name2' column of the gene annotation table when reading the gene IDs in the files given through --gn-groups. This flag is meaningful only with --gn-groups.",default=False)
    optparser.add_option("--dump", action="store_true", dest="dump",\
                         help="Whether to save the raw profiles of near TSS, TTS, and gene body. The file names have a suffix of 'TSS', 'TTS', and 'gene' after the name.",default=False)
    
    return optparser


def opt_validate (optparser):
    """Validate options from a OptParser object.

    Ret: Validated options object.
    """
    (options,args) = optparser.parse_args()
    
    # if gdb not given, print help, either BED or WIG must be given 
    if not options.gdb and not options.bed and not options.wig:
        optparser.print_help()
        sys.exit(1)
    elif not options.gdb:
        error('A gene table file must be given through -g (--gt).')
        sys.exit(1)
    elif options.gdb and not options.bed and not options.wig:
        error('Either a BED file or a WIG file must be given.')
        sys.exit(1)
   
    ##
    # check what inputs are given and determine which modules will operate
    ##
    
    #
    # check gene annotation table database
    # 
    
    #quotes bug fix
    if options.gdb and'"' in options.gdb:
        options.gdb = options.gdb.replace('"','')
    if options.bed and '"' in options.bed:
        options.bed = options.bed.replace('"','')
    if options.wig and '"' in options.wig:
        options.wig = options.wig.replace('"','')
    if options.ebed and '"' in options.ebed:
        options.ebed = options.ebed.replace('"','')
    if options.name and '"' in options.name:
        options.name = options.name.replace('"','')
    if options.gn_groups and '"' in options.gn_groups:
        options.gn_groups = options.gn_groups.replace('"','')
    if options.gn_names and '"' in options.gn_names:
        options.gn_names = options.gn_names.replace('"','')
                
    HAVELOCALGDB = os.path.isfile(options.gdb)
    if not HAVELOCALGDB:
        error("No such gene table file as '%s'" %options.gdb)
        sys.exit(1)
    else:
        options.gdbtype = 'localdb'
        options.Host = None
        options.User = None
    
    #
    #check the ChIP bed file
    #
    if options.bed:
        HAVEBED = os.path.isfile(options.bed)
        if not HAVEBED:
            error("Check -b (--bed). No such bed file as '%s'" %options.bed)
            sys.exit(1)
        if os.path.getsize(options.bed) > 5000000:
            warnings.warn("ChIP bed file size may be too large to run CEAS with. Make sure it is a 'peak' file!")
            #error("ChIP bed file size is too big to handle! The file size is limmited to 5M bytes.")
            #sys.exit(1)
    else: HAVEBED = False
    
    #
    # check the wig file
    # 
    if options.wig:
        HAVEWIG=os.path.isfile(options.wig)
        if not HAVEWIG:
            error("Check -w (--wig). No such wig file as '%s'" %options.wig)
            sys.exit(1)
    else: HAVEWIG=False
        
    # check background annotation
    BG = options.bg
    REBG = False
    PF = False
    CHIPANNOT = False
    
    #
    # determine do ChIP annotation and re-do genome background annotation
    #
    if HAVEBED:
        CHIPANNOT = True
        if BG and HAVEWIG:
            REBG = True

    #   
    # determine do wig profiling
    #
    if HAVEWIG:
        if HAVELOCALGDB: 
            PF = True
   
    #
    # non-coding regions
    #
    EBEDANNOT = False
    if options.ebed and CHIPANNOT:
        if not os.path.isfile(options.ebed):
            error("Check -e (--ebed). No such file as '%s'" %options.ebed)
            sys.exit(0)
        else:
            EBEDANNOT = True
    
    #
    # set options to activate CEAS modules
    #
    options.chipannot = CHIPANNOT
    options.rebg = REBG
    options.pf = PF
    options.ebedannot = EBEDANNOT
    
    ##
    # handle other options
    ##
    
    # 
    # get the experiment name
    #
    # if options.name is not given, BED and WIG file names will be used in order
    if not options.name:
        if HAVEBED:
            options.name=os.path.split(options.bed)[-1].rsplit('.bed',2)[0]
        elif HAVEWIG:
            options.name=os.path.split(options.wig)[-1].rsplit('.wig',2)[0]
    
    #
    # ChIP annotation related parameters
    #    
    options.bg_res=100
    options.chip_res = 1000
    
    # promoter downstream lengths and bidirectional promoter lengths
    try:
    	options.sizes= map(int, options.sizes.rsplit(','))
    	options.bisizes= map(int, options.bisizes.rsplit(','))
    except ValueError:
    	error('Only integer values are accepted for --sizes or --bisizes and numbers must be comma-separated w/o space')
    	sys.exit(0)
    
    # only three values or one value can be given	
    if len(options.sizes) !=3 and len(options.sizes) !=1:
    	error('Three comma-separated numbers or a single number can be given for --sizes')
    	sys.exit(0)
    
    # only two values or one value can be given	
    if len(options.bisizes) !=2 and len(options.bisizes) !=1:
    	error('Two comma-separated numbers or a single number can be given for --bisizes')
    	sys.exit(0)
    
    # saturate the values with 1000 for promoter and downstream and 20000 for bidirectional promoter
    options.sizes = map(min, [10000]*len(options.sizes), options.sizes)
    options.bisizes = map(min, [20000]*len(options.bisizes), options.bisizes)
    	
    # if a single value is given, split into three equal fractions
    if len(options.sizes) == 1:
    	n = 3
    	options.sizes = [options.sizes[0]*i/n for i in range(1, n+1)]
    options.sizes.sort()
    options.sizes = tuple(options.sizes)
    
    if len(options.bisizes) == 1:
    	n = 2
    	options.bisizes = [options.bisizes[0]*i/n for i in range(1, n+1)]
    options.bisizes.sort()
    options.bisizes = tuple(options.bisizes)    
        
    #
    # gene-centered annotatino related parameters
    #
    
    options.span = max(1, options.span)
    
    #
    # Average profiling related parameters
    #
    #check if name2 is going to be used instead of name
    if options.name2 and options.pf and options.gn_groups:
        options.name2 = True
    else:
        options.name2 = False
                    
    # check the gene group files    
    if options.pf and options.gn_groups:
        parsed=options.gn_groups.rsplit(',')
        for p in parsed:
            if not os.path.isfile(p):
                error("Check --gn-groups. No such file as '%s'" %p)
                sys.exit(0)
        options.gn_groups=parsed
        
        # gene group names. If not given, Group 1, Group 2, ... Group n will be used
        if options.gn_names:
            parsed_names=options.gn_names.rsplit(',')
            if len(parsed_names) < len(options.gn_groups):
                error('There must be the equal or more group names to or than gene groups')
                sys.exit(0)
            options.gn_names=parsed_names
        else:
            options.gn_names=[]
            for i in range(len(options.gn_groups)):
                options.gn_names.append('Group %d' %(i+1))
    
    
    # profiling resolution
    options.pf_res = max(1, options.pf_res)
    
    # relative distance
    options.rel_dist = max(options.rel_dist, options.pf_res)
    
    # metagene_size
    options.metagene_size = 3000
    options.catexon_size = options.metagene_size/2
    options.catintron_size = options.metagene_size/2
    
    # exon length and intron lengths 
    options.epercentlowers=[10, 35, 65]    # in percent
    options.epercentuppers=[35, 65, 90]    # in percent
    options.ipercentlowers=[10, 35, 65]    # in percent
    options.ipercentuppers=[35, 65, 90]    # in percent
    
    #
    # dummy parameters
    #
    options.elowers = None
    options.euppers = None
    options.ilowers = None
    options.iuppers = None
    
    options.metaexonsizes = None
    options.metaintronsizes = None
    
    options.n_peaks = None	# the number of top n peaks to plot in chromosomal view of the peaks: Default None, which means only downsampling will be applied.
        
    #
    # dump
    #

    if options.dump:
        options.out = {}
        options.out['upstreams'] = open(options.name + '_dump_TSS.txt', 'w')
        options.out['downstreams'] = open(options.name + '_dump_TTS.txt', 'w')
        options.out['genes'] = open(options.name + '_dump_gene.txt', 'w')
        
        if options.gn_groups:
            options.outs = {}

            # group names
            gn_names = [re.sub(r' ', '_', gn_name) for gn_name in options.gn_names]
            options.outs['upstreams'] = [open(options.name + "_" + gn_name + "_dump_TSS.txt", "w") for gn_name in gn_names]
            options.outs['downstreams'] = [open(options.name + "_" + gn_name + "_dump_TTS.txt", "w") for gn_name in gn_names]
            options.outs['genes'] = [open(options.name + "_" + gn_name + "_dump_gene.txt", "w") for gn_name in gn_names]
        dumpprof = 'On'
    else:
        dumpprof = 'Off'

    #
    # make a txt of parameter setting
    #
    
    # basic parameters
    if options.chipannot: chipannot = 'On'
    else: chipannot = 'Off'
    if options.pf: avpf = 'On'
    else: avpf = 'Off'
    options.argtxt = "\n".join((
                                "# ARGUMENTS: ", \
                                "# name = %s" % (options.name),\
                                "# gene annotation table = %s" % (options.gdb),\
                                "# BED file = %s" % (options.bed),\
                                "# WIG file = %s" % (options.wig),\
                                "# extra BED file = %s" % (options.ebed),\
                                "# ChIP annotation = %s" % (chipannot),\
                                "# gene-centered annotation =  %s" %(chipannot),\
                                "# average profiling = %s" %(avpf),\
                                "# dump profiles = %s" %(dumpprof)))
    
    # if ChIP region annotation is running
    if options.chipannot:
        options.argtxt += "\n"
        options.argtxt += "\n".join(("# re-annotation for genome background (ChIP region annotation) = %s" %(str(options.rebg)),\
                                     "# promoter sizes (ChIP region annotation) = " + ",".join(["%d" %p for p in options.sizes]) + " bp",\
                                     "# downstream sizes (ChIP region annotation) = " + ",".join(["%d" %d for d in options.sizes]) + " bp",\
                                     "# bidrectional promoter sizes (ChIP region annotation) = " + ",".join(["%d" %d for d in options.bisizes]) + " bp",\
                                     "# span size (gene-centered annotation) = %d bp" %(options.span)))
    
    # if average profilng is running
    if options.pf:
        options.argtxt += "\n"
        options.argtxt += "\n".join(("# profiling resolution (average profiling) = %d bp" %(options.pf_res),\
                                     "# relative distance wrt TSS and TTS (average profiling) = %d bp" % (options.rel_dist)))
        if options.gn_groups:
            options.argtxt += "\n"
            options.argtxt += "# gene groups (average profiling) = %s" %", ".join(options.gn_groups)     
                         
    return options


def get_min_max_lengths(GeneT, epercentlimit, ipercentlimit, minexonlen, minintronlen):
    """Return the minimum and maximum lengths of exons or introns in the gene annotation table
    
    Parameters:
    1. GeneT: gene annotation table
    2. epercentlimit: [lower limit for exon length, upper limit for exon length] to consider. lower limit and upper limit must be percentage values (0-100)
    3. ipercentlimit: [lower limit for intron length, upper limit for exon lentht] to consider. 
    4. minexonlen: mininum exon length to consider
    5. minintronlen: minimum intron length to consider
    """
    
    exonLens, intronLens=GeneT.get_exon_intron_lens()
    exon_enough_long, intron_enough_long = corelib.get_certain_part(exonLens, percentlimit=epercentlimit), corelib.get_certain_part(intronLens, percentlimit=ipercentlimit)
    exonLenLim, intronLenLim = [max(minexonlen, exon_enough_long[0]), exon_enough_long[-1]], [max(minintronlen, intron_enough_long[0]), intron_enough_long[-1]]

    return exonLenLim, intronLenLim


def return_med_gene(GeneT, pf_res):
    """Return the median gene length of the given gene annotation table.
    
    The nearest multiple of pf_res to the median gene length will be returned.
    
    Parameters:
    1. GeneT: the gene annotation table
    2. pf_res: the profiling resolution
    
    """
    
    gLens = GeneT.get_gene_lens()
    medgLen = corelib.median(gLens)
    
    return corelib.find_nearest_multiple(medgLen, pf_res)


def return_med_catexon_catintron(GeneT, pf_res):
    """Return the concatenated exons and introns sizes for scaling
    
    Parameters:
    1. GeneT: the gene annotation table to consider
    2. pf_res: profiling resolution
    
    """
        
    catexonLens, catintronLens =GeneT.get_cat_exon_intron_lens()
    medcatexonLen, medcatintronLen = corelib.median(catexonLens), corelib.median(catintronLens)
    medcatexonLen, medcatintronLen = corelib.find_nearest_multiple(medcatexonLen, pf_res), corelib.find_nearest_multiple(medcatintronLen, pf_res)

    return medcatexonLen, medcatintronLen


def return_med_exons_introns(GeneT, eplowers, epuppers, iplowers, ipuppers):
    """Return the median exon lengths and intron lengths
    
    Parameters:
    1. GeneT: genome annotation table
    2. eplowers: a list of numbers (1-100) that indicate lower percentage limits for exon length
    3. epuppers: a list of numbers (1-100) that indicate upper percentage limits for exon length
    4. iplowers: a list of numbers (1-100) that indicate lower percentage limits for intron length
    5. ipuppers: a list of numbers (1-100) that indicate upper percentage limits for intron length
    
    """
    
    exonLens, intronLens = GeneT.get_exon_intron_lens()
    elowers, medexonsizes, euppers = corelib.get_boundaries_medians(exonLens, lowers = eplowers, uppers = epuppers)
    ilowers, medintronsizes, iuppers = corelib.get_boundaries_medians(intronLens, lowers = iplowers, uppers = ipuppers)
    
    return elowers, medexonsizes, euppers, ilowers, medintronsizes, iuppers


def determine_exon_intron_sizes(GeneT, options):
    """Determine the exon and intron sizes to consider in exon-intron average profiling"""
    
    # get the exon lengths and intron lengths when either elowers or ilowers is not given.
    if not (options.elowers and options.ilowers):
        elowers, medexonsizes, euppers, ilowers, medintronsizes, iuppers = return_med_exons_introns(GeneT, options.epercentlowers, options.epercentuppers, options.ipercentlowers, options.ipercentuppers)
    
    # update the options with the exonsizes and intronsizes
    if options.elowers and not options.ilowers:
        options.ilowers = ilowers
        options.iuppers = iuppers
        medexonsizes = map(lambda x,y: (x+y)/2, options.elowers, options.euppers)
    elif not options.elowers and options.ilowers:
        options.elowers = elowers
        options.euppers = euppers
        medintronsizes  = map(lambda x,y: (x+y)/2, options.ilowers, options.iuppers)
    elif not options.elowers and not options.ilowers:
        options.elowers = elowers
        options.euppers = euppers
        options.ilowers = ilowers
        options.iuppers = iuppers
    else:
        medexonsizes = map(lambda x,y: (x+y)/2, options.elowers, options.euppers)
        medintronsizes = map(lambda x,y: (x+y)/2, options.ilowers, options.iuppers)
    
    # approximate the exonsizes to the nearest the multiple of the profiling resolution
    n_ranges_e = len(options.elowers)
    n_ranges_i = len(options.ilowers)
    options.metaexonsizes = map(corelib.find_nearest_multiple, map(max, medexonsizes, [2*options.pf_res]*n_ranges_e), [options.pf_res]*n_ranges_e)
    options.metaintronsizes = map(corelib.find_nearest_multiple, map(max, medintronsizes, [2*options.pf_res]*n_ranges_i), [options.pf_res]*n_ranges_i)
    
    return options    
    
    
def filter_chroms(chroms,regex):
    """Get rid of chromosome names with a user-specified re
    
    Parameters:
    1. chroms: chromosome names
    2. re: regular expression as a raw string
    
    Return:
    filtered_chrom: chromosome names after filtering
    
    """
    filtered_chroms=[]
    for chrom in chroms:
        if not re.search(regex, chrom):
            filtered_chroms.append(chrom)
    
    return filtered_chroms


def write_txts(outs, txts):
    """Write multiple txts to multiple files
    
    Arguments
    1. outs: a list of file handlers
    2. txts: a list of texts to write out
    """
    
    for out, txt in itertools.izip(outs, txts):
        out.write(txt)


def close_dump_files(out):
    """Close the file handlers of the dump files
    """

    out.close()
    

def close_dump_mult_files(outs):
    """Close the file handers of multiple dump files
    """
    
    for out in outs:
        out.close()

    
def _get_bgroi(GenomeBGP,GenomeBGS,roi,bg_res=100):
    """Get the background annotation for regions of interest given through -e (or --ebed) option
    
    Parameters:
    1. GenomeGBP: a P object (see inout.py) of genome background annotation. This will be modified by this function and returned.
    2. GenomeGBS: a SummaryGBG object (see inout.py) of genome background annotation
    2. roi: a Bed object of regions of interest
    3. bg_res: genome background annotation resolution (default=100bp)
    
    """
    
    # take the union of the roi regions, just in case these regions overlap each other
    u_roi = _take_union_of_bed(roi)
    
    # sampler
    Sampler=sampler.ChIPSampler()
    roisamp=Sampler.sample(bed=u_roi,resolution=bg_res)
    
    chroms=set(GenomeBGP.get_chroms()).intersection(roisamp.keys())
    bgroi={}
    whole=0
    for chrom in chroms:
        num_this_chr=len(roisamp[chrom])
        bgroi[chrom]=num_this_chr
        whole+=num_this_chr
    bgroi['whole']=whole
    
    for chrom in bgroi.keys():
        try:
            GenomeBGP[chrom]['roi']=1.0*bgroi[chrom]/GenomeBGS[chrom]['Ns']
        except ZeroDivisionError:
            pass
        except KeyError:
            pass
    
    return GenomeBGP

def _take_union_of_bed(bed):
    """Take the union of the bed regions and return it as a new Bed object. Only chr, start, and end will remain in the new Bed object.
    
    Arguments:
    1. bed: a Bed object
    """
    
    chroms = bed.get_chroms()
    union = inout.Bed()
    union.bed = {}
    for chrom in chroms:
        u = corelib.union_intervals([bed[chrom]['start'], bed[chrom]['end']])
        union.bed[chrom] = {'start': u[0][:], 'end': u[1][:]}
    
    return union
    

def _interpoloate_gbg(gdb,promoter,bipromoter):
    """In using the pre-computed genome bg model, this function performs linear interpolation of 
    genome-wide enrichments of promoter, bidirectional promoter, and downstream.
    
    Parameters:
    1. gdb: sqlite3 db file. This file must have GenomeBGS and GenomeBGP tables
    2. promoter: promoter length given through options.sizes
    3. bipromoter: bidirectional promoter length given through options.bisizes
    4. downstream: downstream length given through options.sizes
    
    Return
    GP: a P object (see tables.py). This object contains genome bg annotation
    
    """
    
    GenomeBGP=tables.PGBG(name='GenomeBGP',numprom=11,numbiprom=21,numdown=11)
    GenomeBGP.readdb(Db=gdb)
    
    GP=tables.P(name='GP')
    # the given promoter, bipromoter, and downstream lengths
    new={}
    new['promoter'] = promoter
    new['bipromoter'] = bipromoter
    new['downstream'] = promoter
    
    # the model promoter, bipromoter, and downstream lengths
    mod={}
    mod['promoter']=[0, 500]+corelib.seq(fr=1000,to=10000,by=1000)
    mod['bipromoter']=[0, 500]+corelib.seq(fr=1000,to=20000,by=1000)
    mod['downstream']=[0, 500]+corelib.seq(fr=1000,to=10000,by=1000)
    
    for chrom in GenomeBGP.get_chroms():
        GP.init_table(chrom)
        for column in GenomeBGP.columns[1:]:
            if column not in ['promoter','bipromoter','downstream']:
                GP[chrom][column]=GenomeBGP[chrom][column]
            else:
                vals=[0.0]+GenomeBGP[chrom][column]
                interpol=[]
                for x in new[column]:

                    # first check if the value is in the genome background list
                    try:
                        i = mod[column].index(x)
                        interpol.append(vals[i])
                    except ValueError: # if cannot find, do linear interpolation.
                        i=corelib.findbin(x, mod[column])
                        try:
                            interpol.append(corelib.lininterpol([mod[column][i],vals[i]], [mod[column][i+1],vals[i+1]],x))
                        except IndexError: # in case that x is equal to or larger than the upper bound (e.g. >= 10000 in promoter or downstream)
                            interpol.append(corelib.lininterpol([mod[column][i],vals[i]], [mod[column][i],vals[i]],x))
	
                GP[chrom][column]=interpol
                
    return GP
                    
                    
if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        warn("User interrupts me! ;-) See you!")
        sys.exit(0)