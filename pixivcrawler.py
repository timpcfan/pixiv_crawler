import configparser
import requests
import logging
import pickle
import json
import time
import os
import re
import utils

class PixivCrawler(object):

    CONFIG_PATH = 'config.ini'
    LOGIN_URL = 'https://accounts.pixiv.net/login?lang=zh&source=pc&view_type=page&ref=wwwtop_accounts_index'
    POST_URL = 'https://accounts.pixiv.net/api/login?lang=zh'
    MAIN_URL = 'https://www.pixiv.net'
    ILLUST_URL = 'https://www.pixiv.net/member_illust.php?mode=medium&illust_id='

    def __init__(self):
        """initialize the config and global vars"""

        logging.basicConfig(level=logging.INFO,
                            format='%(asctime)s %(levelname)s %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S')

        # load config
        self.conf = configparser.ConfigParser(interpolation=None)
        self.conf.read(PixivCrawler.CONFIG_PATH, encoding='utf-8')
        self.username = self.conf.get('pixiv', 'username')
        self.password = self.conf.get('pixiv', 'password')
        self.download_dir_path = self.conf.get('system', 'download_dir_path')
        self.cookies_path = self.conf.get('system', 'cookies_path')
        self.use_filter = self.conf.getboolean('filter', 'use_filter')
        self.like_more_than = self.conf.getint('filter', 'like_more_than')
        self.bookmark_more_than = self.conf.getint('filter', 'bookmark_more_than')
        self.tags_include = self.conf.get('filter', 'tags_include').split(',')
        self.tags_mode = self.conf.get('filter', 'tags_mode')  # all/any
        self.tags_exclude = self.conf.get('filter', 'tags_exclude').split(',')
        self.min_width = self.conf.getint('filter', 'min_width')
        self.min_height = self.conf.getint('filter', 'min_height')
        self.enable_multiple_picture_download = self.conf.getboolean('filter', 'enable_multiple_picture_download')
        self.download_limit = self.conf.getint('system', 'download_limit')
        self.separate_folder_by_like_count = self.conf.getboolean('system', 'separate_folder_by_like_count')
        tmp = self.conf.get('system', 'separate_level').split(',')
        self.separate_level = [int(i.strip()) for i in tmp]

        if not os.path.exists(self.download_dir_path):
            os.makedirs(self.download_dir_path)

        # init
        self.session = requests.session()
        self.crawled_ids = self._init_crawled_ids()
        self.to_crawl = set()
        self.headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/68.0.3440.106 Safari/537.36',
            'Referer': 'https://accounts.pixiv.net/login?lang=zh&source=pc&view_type=page&ref=wwwtop_accounts_index'
        }

        self.login()

        if self.conf.getboolean('system', 'auto_start'):
            self.spider(self.conf.get('system', 'start_illust_id'))

    def _init_crawled_ids(self):
        """load the ids that already downloaded"""

        return set([re.sub(r'_p\d+.*', '', i) for i in utils.list_all_file_name_in_dir(self.download_dir_path)])

    def login(self):
        """log into the Pixiv by cookies or username&password"""

        if os.path.exists(self.cookies_path):
            logging.info('cookies file found and try to load...')
            self._load_cookies()
            if not self._login_check():
                self._login()
        else:
            logging.info('no cookies file and try to login as the first time')
            r = self.session.get(PixivCrawler.MAIN_URL)
            self._login()

    def _login(self):
        """log into the Pixiv by username&password and save the cookies"""

        logging.info('trying to login as username:' + self.username + ' and password: ' + self.password)
        r = self.session.get(PixivCrawler.LOGIN_URL, headers=self.headers)
        post_key = re.findall(r'<input type="hidden" name="post_key" value="(.*?)">', r.text)[0]

        form_data = {
            'pixiv_id': self.username,
            'password': self.password,
            'post_key': post_key,
            'source': 'pc',
            'return_to': 'https://www.pixiv.net',
        }

        r = self.session.post(PixivCrawler.POST_URL, data=form_data, headers=self.headers)
        r = json.loads(r.text)
        if not r['error']:
            logging.info('login successfully!')
        else:
            logging.error('login error!')
            exit(0)
        self._redirect(PixivCrawler.MAIN_URL)
        self._save_cookies()

    def _redirect(self, to_url):
        """redirect to the specific url and maintain the headers"""

        ret = self.session.get(to_url, headers=self.headers)
        if ret.status_code == 200:
            logging.info('redirected to "' + to_url + '" successfully!')
            self.headers['Referer'] = to_url
        else:
            logging.error('redirect to "' + to_url + '" occurred ERROR with the code:' + str(ret.status_code))
        return ret

    def _parse_pic_info(self, html):
        """parse the picture introduce page's information"""

        ret = {}
        try:
            ret['download_url'] = re.sub(r'\\', '', re.findall(r'"original":"(.*?)"', html)[0])
            ret['filename'] = re.findall(r'/([\d\w_]+\.\w{3})$', ret['download_url'])[0]
            ret['title'] = re.findall(r'"illustTitle":"(.*?)"', html)[0].encode('utf-8').decode('unicode_escape')
            ret['id'] = re.findall(r'"illustId":"(.*?)"', html)[0]
            l = re.findall(r'"width":(\d+),"height":(\d+),"pageCount":(\d+),"bookmarkCount":(\d+),'
                           r'"likeCount":(\d+),"commentCount":(\d+)', html)[0]
            ret['width'], ret['height'], ret['page_count'], ret['bookmark_count'],\
                ret['like_count'], ret['comment_count'] = [int(i) for i in l]
            ret['tags'] = [i.encode('utf-8').decode('unicode_escape') for i in re.findall(r'"tag":"(\S*?)"', html)]
        except Exception as e:
            logging.error('parse fail...' + str(e))
            return None

        logging.info('parse finished : ' + str(ret))
        return ret

    def _save_cookies(self):
        """save cookies as file"""

        d = self.session.cookies.get_dict()
        with open(self.cookies_path, 'wb') as f:
            pickle.dump(d, f)
        logging.info('cookies save as ' + self.cookies_path)

    def _load_cookies(self):
        """load cookies from file"""

        with open(self.cookies_path, 'rb') as f:
            d = pickle.load(f)
        self.session.cookies.update(d)
        logging.info('cookies set as ' + self.cookies_path)

    def _login_check(self):
        """check if it login successfully"""

        r = self.session.get(PixivCrawler.MAIN_URL)
        result = re.findall(r"login: '(\w*?)',", r.text)[0]
        if result == 'yes':
            logging.info('login status: yes')
            return True
        else:
            logging.info('login status: no')
            return False

    def grab_pic_by_id(self, illust_id, use_filter=False):
        """download the picture with specific illust_id"""

        pic_url = PixivCrawler.ILLUST_URL + str(illust_id)  # build the whole url of the specific picture
        r = self._redirect(pic_url)
        pic_info = self._parse_pic_info(r.text)

        # with open('pic_example_2.html', 'wb') as f:
        #     f.write(r.content)

        if not pic_info:
            logging.error('grab picture fail (no pic_info)')
            return False

        # filtering
        if use_filter and not self._filter(pic_info):
            logging.error('grab picture fail (not reach requirement)')
            return False

        # get the download path
        download_path = self.download_dir_path
        if self.separate_folder_by_like_count:
            dir_name = self._get_separate_folder_name(pic_info['like_count'])
            if dir_name:
                download_path = os.path.join(download_path, dir_name)

        # downloading multiple pictures
        for i in range(0, pic_info['page_count']):
            filename = re.sub(r'p0', 'p' + str(i), pic_info['filename'])
            download_url = re.sub(r'p0', 'p' + str(i), pic_info['download_url'])
            self._download(download_url, filename, download_path)
        return True

    def _filter(self, pic_info):
        """test if the pic_info satisfy the config setting"""

        if pic_info['page_count'] > 1 and not self.enable_multiple_picture_download:
            logging.info('not reach requirement : not allowing multiple pictures downloading!')
            return False

        if pic_info['like_count'] <= self.like_more_than:
            logging.info('not reach requirement : like_count = {} <= {}, skipping'
                         .format(pic_info['like_count'], self.like_more_than))
            return False

        if pic_info['bookmark_count'] <= self.bookmark_more_than:
            logging.info('not reach requirement : bookmark_count = {} <= {}, skipping'
                         .format(pic_info['bookmark_count'], self.bookmark_more_than))
            return False

        if pic_info['width'] < self.min_width:
            logging.info('not reach requirement : width {} < {}'.format(pic_info['width'], self.min_width))

        if pic_info['height'] < self.min_height:
            logging.info('not reach requirement : height {} < {}'.format(pic_info['height'], self.min_height))

        for tag in self.tags_exclude:
            if tag in pic_info['tags']:
                logging.info('not reach requirement : tag "{}" in tags_exclude, skipping'.format(tag))
                return False

        if str(self.tags_mode).lower() == 'all':
            rest = [i for i in self.tags_include if i not in pic_info['tags']]
            if rest:
                logging.info('not reach requirement : tags "{}" not in the picture\'s tags'.format(rest))
                return False
        else:
            union = [i for i in self.tags_include if i in pic_info['tags']]
            if not union:
                logging.info('not reach requirement : tags required are "{}" but the picture\'s tags are "{}"'
                             .format(self.tags_include, pic_info['tags']))
                return False

        logging.info('requirement satisfied! ' + str(pic_info))
        return True

    def _download(self, download_url, filename, download_path):
        """download file with specific url and save the file with the name of filename in download_path"""

        start_time = time.time()  # start to timing
        r = self.session.get(download_url, headers=self.headers)  # downloading the picture
        if not r.status_code == 200:
            logging.error('downloading picture fail at ' + download_url)
            return
        if not os.path.exists(download_path):
            os.makedirs(download_path)
        dlpath = os.path.join(download_path, filename)
        with open(dlpath, 'wb') as f:
            file_size = f.write(r.content)
        time_consumed = time.time() - start_time
        speed = file_size / 1000 / time_consumed
        logging.info('downloaded successfully at the speed of {:.2f}KB/s in {:.2f}s, save as {}'
                     .format(speed, time_consumed, dlpath))

    def _get_separate_folder_name(self, like_count):
        """ get the separated folder name using specific like_count"""

        if len(self.separate_level) == 0:
            return None
        elif len(self.separate_level) == 1:
            if like_count >= self.separate_level[0]:
                return str(self.separate_level[0]) + '+'
            else:
                return str(self.separate_level[0]) + '-'
        elif len(self.separate_level) == 2:
            r1, r2 = self.separate_level
            if like_count < r1:
                return str(r1) + '-'
            elif like_count < r2:
                return '{}-{}'.format(r1, r2)
            else:
                return str(r2) + '+'
        else:
            r = self.separate_level
            if like_count < r[0]:
                return str(r[0]) + '-'
            prev = r[0]
            for i in r[1:]:
                if like_count < i:
                    return '{}-{}'.format(prev, i)
                prev = i
            return str(r[-1]) + '+'

    def _get_recommend_illust_ids(self, illust_id):
        """get the list of recommend illust_ids of the picture with specific illust_id"""

        url = "https://www.pixiv.net/ajax/illust/{}/recommend/init?limit=1".format(illust_id)
        r = self.session.get(url, headers=self.headers).json()
        if r['error']:
            logging.error('get recommend list error: ' + r['message'])
            return []
        all_ids = [r['body']['illusts'][0]['workId']] + r['body']['nextIds']
        logging.info('got {} recommends'.format(len(all_ids)))
        return all_ids

    def add_id(self, illust_id, is_force=False):
        """add new illust_id to the to_crawl set if the picture with the illust_id hasn't been downloaded"""

        if len(self.to_crawl) >= 1000:
            return False
        if illust_id not in self.crawled_ids or is_force:
            self.to_crawl.add(illust_id)
            logging.info('new id added :{}, {} ids to go...'.format(illust_id, len(self.to_crawl)))
            return True
        return False

    def add_ids(self, ids):
        """add a list of illust_id at one time"""

        limit = 10
        count = 0
        for id in ids:
            if count >= limit:
                break
            if self.add_id(id):
                count += 1
        logging.info('{} ids added!'.format(count))

    def pop_id(self):
        """random choice a illust_id from the to_crawl set and add it to the crawled_ids set"""

        if len(self.to_crawl) > 0:
            id = self.to_crawl.pop()
            self.crawled_ids.add(id)
            logging.info('get id :' + str(id))
            return id
        return None

    def spider(self, start_illust_id):
        """start the crawling program to continuously download pictures with a start illust_id"""

        self.add_id(start_illust_id, is_force=True)
        count = 0
        while True:
            if len(self.to_crawl) == 0 or count > self.download_limit:
                break
            next_id = self.pop_id()
            try:
                logging.info('now crawling the {} picture with the id {}...'.format(count + 1, next_id))
                if self.grab_pic_by_id(next_id, use_filter=self.use_filter):
                    count += 1
                    rec_ids = self._get_recommend_illust_ids(next_id)
                    self.add_ids(rec_ids)
            except Exception as e:
                print(e)
        logging.info('the to_crawl set is empty, crawling finished, total crawled {} ids'.format(count))


if __name__ == '__main__':
    pixiv = PixivCrawler()
