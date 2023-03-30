sleep 5
now=`date +%F`
previous=`date --date='30 days ago' +%F`
rm -f ./log/shellScript-$previous.log 2>&1
python4 main.py >> ./log/shellScript-$now.log 2>&1